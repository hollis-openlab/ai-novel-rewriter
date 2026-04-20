from __future__ import annotations

import html
import hashlib
import json
import mimetypes
import re
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.contracts.api import NovelDetailResponse, NovelListResponse
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db import Chapter as ChapterRow
from backend.app.db import Novel, StageRun, Task, get_db_session
from backend.app.models.core import Chapter, FileFormat, RewriteResult, StageName, StageRunInfo, StageStatus
from backend.app.services.import_pipeline import import_novel_file

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

router = APIRouter(prefix="/novels", tags=["novels"])


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "gbk", "gb2312"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AppError(
        code=ErrorCode.UNSUPPORTED_FORMAT,
        message="Failed to decode text file using utf-8/gbk/gb2312",
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    )


def _build_default_pipeline_status(novel_id: str) -> dict[StageName, StageRunInfo]:
    return {
        stage: StageRunInfo(
            id=f"{novel_id}-{stage.value}-1",
            run_seq=1,
            stage=stage,
            status=StageStatus.PENDING,
            is_latest=True,
        )
        for stage in StageName
    }


def _normalize_stage_status(
    raw: str | StageStatus,
    *,
    chapters_total: int = 0,
    chapters_done: int = 0,
    completed_at: object | None = None,
) -> StageStatus:
    status = raw if isinstance(raw, StageStatus) else StageStatus(str(raw))
    if status == StageStatus.STALE:
        return status  # Return STALE as-is so the frontend can display it
    return status


REWRITE_ACCEPTED_STATUSES = {"accepted", "completed", "accepted_edited"}
FINAL_EXPORT_FORMATS = {"txt", "epub", "compare"}
FINAL_EXPORT_SCOPES = {"all", "chapter_range", "rewritten_only"}
FINAL_EXPORT_REFLOW_MODES = {"none", "sentence_linebreak"}
SENTENCE_LINEBREAK_RE = re.compile(r"([。！？!?…]+[”’\"'）)\]】》」』]*)(?![\n\r])")
ASSEMBLE_RESULT_FILENAME = "assemble_result.json"


def _safe_ascii_download_filename(filename: str, *, fallback_base: str = "novel-export") -> str:
    basename = Path(filename).name.strip() or filename.strip()
    suffix = Path(basename).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
        suffix = ""
    stem = Path(basename).stem if suffix else basename
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    if not safe_stem:
        safe_stem = fallback_base
    return f"{safe_stem}{suffix}"


def _attachment_content_disposition(filename: str, *, fallback_base: str = "novel-export") -> str:
    fallback = _safe_ascii_download_filename(filename, fallback_base=fallback_base)
    encoded = quote(filename, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _ai_export_title(title: str) -> str:
    normalized = title.strip()
    if normalized.startswith("【AI】") or normalized.startswith("[AI]"):
        return normalized
    return f"【AI】{normalized}" if normalized else "【AI】novel"


async def _get_task_for_novel(db: AsyncSession, novel_id: str, task_id: str | None) -> Task:
    if task_id is not None:
        row = await db.get(Task, task_id)
        if row is None or row.novel_id != novel_id:
            raise AppError(
                code=ErrorCode.NOT_FOUND,
                message=f"Task `{task_id}` not found for novel `{novel_id}`",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return row

    row = (
        await db.execute(
            select(Task)
            .where(Task.novel_id == novel_id, Task.status == "active")
            .order_by(Task.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        raise AppError(
            code=ErrorCode.NOT_FOUND,
            message=f"Active task for novel `{novel_id}` not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return row


async def _load_task_chapters(db: AsyncSession, task_id: str) -> list[Chapter]:
    rows = (
        await db.execute(
            select(ChapterRow)
            .where(ChapterRow.task_id == task_id)
            .order_by(ChapterRow.chapter_index.asc())
        )
    ).scalars().all()
    return [
        Chapter(
            id=row.id,
            index=row.chapter_index,
            title=row.title,
            content=row.content,
            char_count=row.char_count,
            paragraph_count=row.paragraph_count,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
        )
        for row in rows
    ]


def _load_rewrite_results_map(request: Request, novel_id: str, task_id: str) -> dict[int, list[RewriteResult]]:
    store = request.app.state.artifact_store
    aggregate_path = store.stage_dir(novel_id, task_id, "rewrite") / "rewrites.json"
    if not aggregate_path.exists():
        return {}
    payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    chapters = payload.get("chapters", [])
    if not isinstance(chapters, list):
        return {}

    mapped: dict[int, list[RewriteResult]] = {}
    for chapter_payload in chapters:
        if not isinstance(chapter_payload, dict):
            continue
        chapter_index = int(chapter_payload.get("chapter_index") or 0)
        if chapter_index < 1:
            continue
        segments = chapter_payload.get("segments", [])
        if not isinstance(segments, list):
            segments = []
        mapped[chapter_index] = [RewriteResult.model_validate(item) for item in segments if isinstance(item, dict)]
    return mapped


def _load_assemble_result_payload(request: Request, novel_id: str, task_id: str) -> dict[str, object]:
    store = request.app.state.artifact_store
    assemble_result_path = store.stage_dir(novel_id, task_id, "assemble") / ASSEMBLE_RESULT_FILENAME
    if not assemble_result_path.exists():
        raise AppError(
            code=ErrorCode.VALIDATION_ERROR,
            message="Assemble stage has not produced export artifacts yet; please run assemble first.",
            status_code=status.HTTP_409_CONFLICT,
            details={"required_stage": StageName.ASSEMBLE.value},
        )
    payload = json.loads(assemble_result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message="assemble_result.json payload is invalid",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return payload


def _stage_run_to_stage_info(stage_row: StageRun) -> StageRunInfo:
    stage_name = StageName(stage_row.stage)
    return StageRunInfo(
        id=stage_row.id,
        run_seq=stage_row.run_seq,
        stage=stage_name,
        status=_normalize_stage_status(
            stage_row.status,
            chapters_total=stage_row.chapters_total or 0,
            chapters_done=stage_row.chapters_done or 0,
            completed_at=stage_row.completed_at,
        ),
        started_at=stage_row.started_at,
        completed_at=stage_row.completed_at,
        error_message=stage_row.error_message,
        run_idempotency_key=stage_row.run_idempotency_key,
        warnings_count=stage_row.warnings_count,
        chapters_total=stage_row.chapters_total,
        chapters_done=stage_row.chapters_done,
        is_latest=True,
    )


def _assemble_result_exists(request: Request, novel_id: str, task_id: str) -> bool:
    assemble_result_path = request.app.state.artifact_store.stage_dir(novel_id, task_id, "assemble") / ASSEMBLE_RESULT_FILENAME
    return assemble_result_path.exists()


def _hydrate_pipeline_status(
    request: Request,
    *,
    novel_id: str,
    task_id: str,
    chapter_indexes: list[int],
    stage_rows: list[StageRun],
) -> dict[StageName, StageRunInfo]:
    pipeline_status = _build_default_pipeline_status(novel_id)
    chapter_count = len(chapter_indexes)

    seen: set[str] = set()
    for stage_row in stage_rows:
        if stage_row.stage in seen:
            continue
        stage_name = StageName(stage_row.stage)
        pipeline_status[stage_name] = _stage_run_to_stage_info(stage_row)
        seen.add(stage_row.stage)

    rewrite_info = pipeline_status.get(StageName.REWRITE)
    if (
        rewrite_info is not None
        and rewrite_info.status in {StageStatus.PENDING, StageStatus.RUNNING, StageStatus.PAUSED}
        and chapter_indexes
    ):
        rewrite_results_map = _load_rewrite_results_map(request, novel_id, task_id)
        if rewrite_results_map:
            all_chapters_completed = True
            for chapter_index in chapter_indexes:
                chapter_results = rewrite_results_map.get(chapter_index)
                if chapter_results is None:
                    all_chapters_completed = False
                    break
                if _rewrite_stage_status_from_results(chapter_results) != StageStatus.COMPLETED:
                    all_chapters_completed = False
                    break
            if all_chapters_completed:
                pipeline_status[StageName.REWRITE] = rewrite_info.model_copy(
                    update={
                        "status": StageStatus.COMPLETED,
                        "chapters_total": rewrite_info.chapters_total or chapter_count,
                        "chapters_done": rewrite_info.chapters_total or chapter_count,
                        "error_message": None,
                    }
                )

    assemble_info = pipeline_status.get(StageName.ASSEMBLE)
    if (
        assemble_info is not None
        and assemble_info.status in {StageStatus.PENDING, StageStatus.RUNNING, StageStatus.PAUSED}
        and _assemble_result_exists(request, novel_id, task_id)
    ):
        pipeline_status[StageName.ASSEMBLE] = assemble_info.model_copy(
            update={
                "status": StageStatus.COMPLETED,
                "chapters_total": assemble_info.chapters_total or chapter_count,
                "chapters_done": assemble_info.chapters_total or chapter_count,
                "error_message": None,
            }
        )

    return pipeline_status


def _rewrite_stage_status_from_results(results: list[RewriteResult]) -> StageStatus:
    if not results:
        return StageStatus.COMPLETED
    statuses = {item.status.value if hasattr(item.status, "value") else str(item.status) for item in results}
    if "failed" in statuses:
        return StageStatus.FAILED
    terminal = {"completed", "accepted", "accepted_edited", "rejected"}
    if statuses.issubset(terminal):
        return StageStatus.COMPLETED
    if "pending" in statuses and any(item in terminal for item in statuses):
        return StageStatus.RUNNING
    return StageStatus.PENDING


def _select_chapter_payloads(
    chapter_payloads: list[dict[str, object]],
    scope: str,
    *,
    chapter_start: int | None,
    chapter_end: int | None,
) -> list[dict[str, object]]:
    if scope == "all":
        return chapter_payloads

    if scope == "chapter_range":
        if chapter_start is None or chapter_end is None:
            raise AppError(
                code=ErrorCode.VALIDATION_ERROR,
                message="chapter_start and chapter_end are required when scope=chapter_range",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if chapter_start < 1 or chapter_end < chapter_start:
            raise AppError(
                code=ErrorCode.VALIDATION_ERROR,
                message="Invalid chapter range",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"chapter_start": chapter_start, "chapter_end": chapter_end},
            )
        return [
            chapter
            for chapter in chapter_payloads
            if chapter_start <= int(chapter.get("chapter_index") or 0) <= chapter_end
        ]

    if scope == "rewritten_only":
        return [chapter for chapter in chapter_payloads if int(chapter.get("rewritten_segments") or 0) > 0]

    raise AppError(
        code=ErrorCode.VALIDATION_ERROR,
        message=f"Unsupported export scope `{scope}`",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _persist_assemble_artifacts(
    request: Request,
    novel_id: str,
    task_id: str,
    assembled_payload: dict[str, object],
) -> dict[str, str]:
    store = request.app.state.artifact_store
    stage_dir = store.stage_dir(novel_id, task_id, "assemble")
    stage_dir.mkdir(parents=True, exist_ok=True)

    output_path = stage_dir / "output.txt"
    output_path.write_text(str(assembled_payload.get("assembled_text") or ""), encoding="utf-8")

    compare_path = stage_dir / "output.compare.txt"
    compare_path.write_text(str(assembled_payload.get("compare_text") or ""), encoding="utf-8")

    report_path = stage_dir / "quality_report.json"
    quality_payload = assembled_payload.get("quality_report")
    report_payload: dict[str, object] = quality_payload if isinstance(quality_payload, dict) else {}
    store.ensure_json(
        report_path,
        {
            **report_payload,
            "novel_id": novel_id,
            "task_id": task_id,
            "stage": "assemble",
        },
    )

    manifest_path = stage_dir / "export_manifest.json"
    manifest_payload = assembled_payload.get("export_manifest")
    store.ensure_json(
        manifest_path,
        {
            **(manifest_payload if isinstance(manifest_payload, dict) else {}),
            "novel_id": novel_id,
            "task_id": task_id,
        },
    )
    return {
        "output_path": str(output_path),
        "compare_path": str(compare_path),
        "quality_report_path": str(report_path),
        "export_manifest_path": str(manifest_path),
    }


def _reflow_sentence_linebreak(text: str) -> str:
    if not text:
        return text
    reflowed = SENTENCE_LINEBREAK_RE.sub(r"\1\n", text)
    reflowed = re.sub(r"[ \t]+\n", "\n", reflowed)
    return re.sub(r"\n{3,}", "\n\n", reflowed)


def _apply_output_reflow(chapters: list[dict[str, object]], *, mode: str) -> list[dict[str, object]]:
    if mode == "none":
        return [dict(chapter) for chapter in chapters]
    if mode != "sentence_linebreak":
        raise AppError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"Unsupported export reflow mode `{mode}`",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    reflowed: list[dict[str, object]] = []
    for chapter in chapters:
        item = dict(chapter)
        item["assembled_text"] = _reflow_sentence_linebreak(str(chapter.get("assembled_text") or ""))
        reflowed.append(item)
    return reflowed


def _risk_txt_header(risk_signature: dict[str, object] | None, quality_report: dict[str, object]) -> str:
    if not risk_signature:
        return ""
    signature = str(risk_signature.get("signature") or "")
    reasons = risk_signature.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    reason_text = ", ".join(str(item) for item in reasons) or "unknown"
    timestamp = str(risk_signature.get("timestamp") or "")
    return (
        "### RISK EXPORT ###\n"
        f"risk_signature: {signature}\n"
        f"timestamp: {timestamp}\n"
        f"reasons: {reason_text}\n"
        f"blocked: {bool(quality_report.get('blocked'))}\n"
        "### END RISK EXPORT ###\n\n"
    )


def _fallback_risk_signature(task_id: str, quality_report: dict[str, object]) -> dict[str, object]:
    reasons_raw = quality_report.get("block_reasons")
    reasons = [str(item) for item in reasons_raw] if isinstance(reasons_raw, list) and reasons_raw else ["QUALITY_GATE_BLOCKED"]

    thresholds = quality_report.get("thresholds")
    threshold_values = thresholds if isinstance(thresholds, dict) else {}
    stats = quality_report.get("stats")
    stat_values = stats if isinstance(stats, dict) else {}

    threshold_comparison = {
        "failed_ratio": {
            "value": float(stat_values.get("failed_ratio") or 0.0),
            "threshold": float(threshold_values.get("max_failed_ratio") or 0.0),
        },
        "warning_count": {
            "value": int(stat_values.get("warning_count") or 0),
            "threshold": int(threshold_values.get("max_warning_count") or 0),
        },
    }
    timestamp = datetime.now(timezone.utc).isoformat()
    signature_payload = {
        "task_id": task_id,
        "reasons": reasons,
        "threshold_comparison": threshold_comparison,
        "timestamp": timestamp,
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "task_id": task_id,
        "stage_run_id": None,
        "reasons": reasons,
        "threshold_comparison": threshold_comparison,
        "timestamp": timestamp,
        "signature": signature,
    }


def _compare_html(chapters: list[dict[str, object]], risk_signature: dict[str, object] | None) -> str:
    banner = ""
    if risk_signature:
        banner = (
            "<div style='padding:12px;border:1px solid #f59e0b;background:#fff7ed;color:#9a3412;margin-bottom:16px;'>"
            f"<strong>Risk Export</strong> signature={html.escape(str(risk_signature.get('signature') or ''))}"
            "</div>"
        )

    sections: list[str] = []
    for chapter in chapters:
        idx = int(chapter.get("chapter_index") or 0)
        title = html.escape(str(chapter.get("title") or f"Chapter {idx}"))
        original_text = html.escape(str(chapter.get("original_text") or ""))
        assembled_text = html.escape(str(chapter.get("assembled_text") or ""))
        sections.append(
            "<section style='margin-bottom:28px;'>"
            f"<h2 style='margin:0 0 12px;font-size:20px;'>Chapter {idx} {title}</h2>"
            "<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;'>"
            "<article style='border:1px solid #e5e7eb;border-radius:8px;padding:10px;white-space:pre-wrap;'>"
            "<h3 style='margin:0 0 8px;font-size:14px;'>Original</h3>"
            f"{original_text}</article>"
            "<article style='border:1px solid #e5e7eb;border-radius:8px;padding:10px;white-space:pre-wrap;'>"
            "<h3 style='margin:0 0 8px;font-size:14px;'>Rewritten</h3>"
            f"{assembled_text}</article>"
            "</div></section>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
        "<title>Compare Export</title></head><body style='font-family:Arial,sans-serif;padding:20px;'>"
        f"{banner}{''.join(sections)}</body></html>"
    )


def _render_xhtml_document(title: str, body_text: str) -> str:
    lines = [line for line in body_text.split("\n\n") if line.strip()]
    paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<!DOCTYPE html>"
        "<html xmlns='http://www.w3.org/1999/xhtml'>"
        "<head><meta charset='utf-8'/><title>"
        f"{html.escape(title)}</title></head><body>"
        f"<h1>{html.escape(title)}</h1>{paragraphs}</body></html>"
    )


def _build_epub_bytes(
    *,
    novel: Novel,
    chapter_payloads: list[dict[str, object]],
    risk_signature: dict[str, object] | None,
    novel_dir: Path,
) -> bytes:
    structure_path = novel_dir / "epub_structure.json"
    structure = json.loads(structure_path.read_text(encoding="utf-8")) if structure_path.exists() else {}

    metadata = structure.get("metadata", {}) if isinstance(structure, dict) else {}
    title = str(metadata.get("title") or novel.title)
    author = str(metadata.get("author") or "Unknown")
    language = str(metadata.get("language") or "zh-CN")
    spine_hrefs = structure.get("spine", []) if isinstance(structure, dict) else []
    if not isinstance(spine_hrefs, list):
        spine_hrefs = []

    chapter_hrefs: list[str] = []
    for idx, _ in enumerate(chapter_payloads, start=1):
        if idx - 1 < len(spine_hrefs):
            chapter_hrefs.append(str(spine_hrefs[idx - 1]))
        else:
            chapter_hrefs.append(f"OEBPS/text/ch_{idx:03d}.xhtml")

    manifest_items: list[tuple[str, str, str, str]] = []
    spine_items: list[str] = []
    for idx, (chapter, href) in enumerate(zip(chapter_payloads, chapter_hrefs, strict=False), start=1):
        item_id = f"ch{idx}"
        chapter_title = str(chapter.get("title") or f"Chapter {idx}")
        body = str(chapter.get("assembled_text") or "")
        manifest_items.append((item_id, href, "application/xhtml+xml", ""))
        spine_items.append(item_id)
        chapter_doc = _render_xhtml_document(chapter_title, body)
        chapter["__epub_doc__"] = chapter_doc

    nav_href = "OEBPS/nav.xhtml"
    manifest_items.append(("nav", nav_href, "application/xhtml+xml", "nav"))

    css_files = structure.get("css_files", []) if isinstance(structure, dict) else []
    if not isinstance(css_files, list):
        css_files = []
    for idx, css_path in enumerate(css_files, start=1):
        css_path = str(css_path)
        source = Path(css_path)
        if not source.exists():
            continue
        css_href = f"OEBPS/styles/style_{idx:03d}.css"
        manifest_items.append((f"css{idx}", css_href, "text/css", ""))

    cover_image = structure.get("cover_image") if isinstance(structure, dict) else None
    cover_href: str | None = None
    if isinstance(cover_image, str):
        source = Path(cover_image)
        if source.exists():
            ext = source.suffix.lower()
            cover_href = f"OEBPS/images/cover{ext or '.jpg'}"
            cover_mime = mimetypes.guess_type(str(source))[0] or "image/jpeg"
            manifest_items.append(("cover-image", cover_href, cover_mime, "cover-image"))

    nav_links = "".join(
        f"<li><a href='{html.escape(href)}'>Chapter {idx}</a></li>" for idx, href in enumerate(chapter_hrefs, start=1)
    )
    nav_doc = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<!DOCTYPE html>"
        "<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
        "<head><meta charset='utf-8'/><title>Table of Contents</title></head>"
        "<body><nav epub:type='toc'><h1>Table of Contents</h1><ol>"
        f"{nav_links}</ol></nav></body></html>"
    )

    risk_meta = ""
    if risk_signature:
        risk_meta = (
            "<meta property='ai-novel:risk-signature'>"
            f"{html.escape(str(risk_signature.get('signature') or ''))}</meta>"
        )

    manifest_entries: list[str] = []
    for item_id, href, media_type, properties in manifest_items:
        properties_attr = f" properties='{html.escape(properties)}'" if properties else ""
        manifest_entries.append(
            f"<item id='{html.escape(item_id)}' href='{html.escape(href)}' media-type='{html.escape(media_type)}'{properties_attr}/>"
        )
    manifest_xml = "".join(manifest_entries)
    spine_xml = "".join(f"<itemref idref='{html.escape(item_id)}'/>" for item_id in spine_items)
    content_opf = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='bookid'>"
        "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        "<dc:identifier id='bookid'>ai-novel-export</dc:identifier>"
        f"<dc:title>{html.escape(title)}</dc:title>"
        f"<dc:creator>{html.escape(author)}</dc:creator>"
        f"<dc:language>{html.escape(language)}</dc:language>"
        f"{risk_meta}</metadata><manifest>{manifest_xml}</manifest><spine>{spine_xml}</spine></package>"
    )

    buffer = BytesIO()
    with ZipFile(buffer, mode="w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0' encoding='utf-8'?>"
            "<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/>"
            "</rootfiles></container>",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr("OEBPS/content.opf", content_opf, compress_type=ZIP_DEFLATED)
        archive.writestr(nav_href, nav_doc, compress_type=ZIP_DEFLATED)
        for chapter, href in zip(chapter_payloads, chapter_hrefs, strict=False):
            archive.writestr(href, str(chapter.get("__epub_doc__") or ""), compress_type=ZIP_DEFLATED)

        for idx, css_path in enumerate(css_files, start=1):
            source = Path(str(css_path))
            if source.exists():
                archive.writestr(f"OEBPS/styles/style_{idx:03d}.css", source.read_bytes(), compress_type=ZIP_DEFLATED)

        if isinstance(cover_image, str) and cover_href is not None:
            source = Path(cover_image)
            if source.exists():
                archive.writestr(cover_href, source.read_bytes(), compress_type=ZIP_DEFLATED)
    return buffer.getvalue()


@router.get("", response_model=NovelListResponse)
async def list_novels(request: Request, db: AsyncSession = Depends(get_db_session)) -> NovelListResponse:
    result = await db.execute(select(Novel).order_by(Novel.imported_at.desc()))
    rows = result.scalars().all()

    active_tasks = (
        await db.execute(
            select(Task.novel_id, Task.id).where(Task.status == "active")
        )
    ).all()
    active_task_by_novel = {str(novel_id): str(task_id) for novel_id, task_id in active_tasks}
    active_task_ids = list(active_task_by_novel.values())

    chapter_indexes_by_task: dict[str, list[int]] = {}
    stage_rows_by_task: dict[str, list[StageRun]] = {}
    if active_task_ids:
        chapter_rows = (
            await db.execute(
                select(ChapterRow.task_id, ChapterRow.chapter_index)
                .where(ChapterRow.task_id.in_(active_task_ids))
            )
        ).all()
        chapter_indexes_by_task = {task_id: [] for task_id in active_task_ids}
        for task_id, chapter_index in chapter_rows:
            chapter_indexes_by_task.setdefault(str(task_id), []).append(int(chapter_index))

        for chapter_indexes in chapter_indexes_by_task.values():
            chapter_indexes.sort()

        stage_rows = (
            await db.execute(
                select(StageRun)
                .where(StageRun.task_id.in_(active_task_ids))
                .order_by(StageRun.task_id.asc(), StageRun.stage.asc(), StageRun.run_seq.desc())
            )
        ).scalars().all()
        for stage_row in stage_rows:
            stage_rows_by_task.setdefault(str(stage_row.task_id), []).append(stage_row)

    data: list[NovelDetailResponse] = []
    for row in rows:
        task_id = active_task_by_novel.get(row.id)
        chapter_indexes = chapter_indexes_by_task.get(task_id, []) if task_id else []
        pipeline_status = (
            _hydrate_pipeline_status(
                request,
                novel_id=row.id,
                task_id=task_id,
                chapter_indexes=chapter_indexes,
                stage_rows=stage_rows_by_task.get(task_id, []),
            )
            if task_id is not None
            else _build_default_pipeline_status(row.id)
        )
        data.append(
            NovelDetailResponse(
                id=row.id,
                title=row.title,
                original_filename=row.original_filename,
                file_format=FileFormat(row.file_format),
                file_size=row.file_size,
                total_chars=row.total_chars,
                imported_at=row.imported_at,
                chapter_count=len(chapter_indexes),
                config_override_json=row.config_override_json,
                task_id=task_id,
                active_task_id=task_id,
                pipeline_status=pipeline_status,
            )
        )

    return NovelListResponse(data=data, total=len(data), page=1, per_page=20)


@router.get("/{novel_id}", response_model=NovelDetailResponse)
async def get_novel(novel_id: str, request: Request, db: AsyncSession = Depends(get_db_session)) -> NovelDetailResponse:
    novel = await db.get(Novel, novel_id)
    if novel is None:
        raise AppError(
            code=ErrorCode.NOT_FOUND,
            message=f"Novel `{novel_id}` not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    pipeline_status = _build_default_pipeline_status(novel_id)
    active_task = (
        await db.execute(
            select(Task)
            .where(Task.novel_id == novel_id, Task.status == "active")
            .order_by(Task.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if active_task is not None:
        chapter_index_rows = (
            await db.execute(
                select(ChapterRow.chapter_index).where(ChapterRow.task_id == active_task.id)
            )
        ).all()
        chapter_indexes = sorted(int(item[0]) for item in chapter_index_rows)
        chapter_count = len(chapter_indexes)

        stage_rows = (
            await db.execute(
                select(StageRun)
                .where(StageRun.task_id == active_task.id)
                .order_by(StageRun.stage.asc(), StageRun.run_seq.desc())
            )
        ).scalars().all()
        pipeline_status = _hydrate_pipeline_status(
            request,
            novel_id=novel_id,
            task_id=active_task.id,
            chapter_indexes=chapter_indexes,
            stage_rows=stage_rows,
        )
    else:
        chapter_count = 0

    return NovelDetailResponse(
        id=novel.id,
        title=novel.title,
        original_filename=novel.original_filename,
        file_format=FileFormat(novel.file_format),
        file_size=novel.file_size,
        total_chars=novel.total_chars,
        imported_at=novel.imported_at,
        chapter_count=chapter_count,
        config_override_json=novel.config_override_json,
        task_id=active_task.id if active_task else None,
        active_task_id=active_task.id if active_task else None,
        pipeline_status=pipeline_status,
    )


@router.get("/{novel_id}/quality-report")
async def get_quality_report(
    novel_id: str,
    request: Request,
    task_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    novel = await db.get(Novel, novel_id)
    if novel is None:
        raise AppError(
            code=ErrorCode.NOT_FOUND,
            message=f"Novel `{novel_id}` not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    task = await _get_task_for_novel(db, novel_id, task_id)
    task_id = task.id

    report_path = request.app.state.artifact_store.stage_dir(novel_id, task_id, "assemble") / "quality_report.json"
    if not report_path.exists():
        raise AppError(
            code=ErrorCode.NOT_FOUND,
            message="quality_report.json not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AppError(
            code=ErrorCode.CONFIG_INVALID,
            message="quality_report.json payload is invalid",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return payload


@router.get("/{novel_id}/export")
async def export_novel(
    novel_id: str,
    request: Request,
    format: str = Query(default="txt"),
    scope: str = Query(default="all"),
    reflow: str = Query(default="none"),
    chapter_start: int | None = Query(default=None, ge=1),
    chapter_end: int | None = Query(default=None, ge=1),
    force: bool = Query(default=False),
    task_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    if format not in FINAL_EXPORT_FORMATS:
        raise AppError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"Unsupported export format `{format}`",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if scope not in FINAL_EXPORT_SCOPES:
        raise AppError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"Unsupported export scope `{scope}`",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if reflow not in FINAL_EXPORT_REFLOW_MODES:
        raise AppError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"Unsupported export reflow mode `{reflow}`",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    novel = await db.get(Novel, novel_id)
    if novel is None:
        raise AppError(
            code=ErrorCode.NOT_FOUND,
            message=f"Novel `{novel_id}` not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    task = await _get_task_for_novel(db, novel_id, task_id)
    assembled_payload = _load_assemble_result_payload(request, novel_id, task.id)

    quality_report = assembled_payload.get("quality_report")
    quality_payload: dict[str, object] = quality_report if isinstance(quality_report, dict) else {}
    if bool(assembled_payload.get("blocked")) and not force:
        raise AppError(
            code=ErrorCode.QUALITY_GATE_BLOCKED,
            message="Assemble quality gate blocked export",
            status_code=status.HTTP_409_CONFLICT,
            details=quality_payload,
        )
    if force and bool(assembled_payload.get("blocked")) and not isinstance(quality_payload.get("risk_signature"), dict):
        quality_payload = {
            **quality_payload,
            "risk_signature": _fallback_risk_signature(task.id, quality_payload),
        }

    chapter_payloads_raw = assembled_payload.get("chapters")
    chapter_payloads = list(chapter_payloads_raw) if isinstance(chapter_payloads_raw, list) else []
    selected_chapters = _select_chapter_payloads(
        [item for item in chapter_payloads if isinstance(item, dict)],
        scope,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )
    if not selected_chapters:
        raise AppError(
            code=ErrorCode.VALIDATION_ERROR,
            message="No chapters selected for export",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"scope": scope, "chapter_start": chapter_start, "chapter_end": chapter_end},
        )

    rendered_chapters = _apply_output_reflow(selected_chapters, mode=reflow)

    risk_signature_raw = quality_payload.get("risk_signature")
    risk_signature = risk_signature_raw if isinstance(risk_signature_raw, dict) else None
    risk_header = _risk_txt_header(risk_signature, quality_payload)
    headers: dict[str, str] = {}
    if risk_signature is not None:
        headers["X-Risk-Signature"] = str(risk_signature.get("signature") or "")

    selected_text = "\n\n".join(str(item.get("assembled_text") or "") for item in rendered_chapters)
    export_title = _ai_export_title(novel.title)

    if format == "txt":
        body = f"{risk_header}{selected_text}" if risk_header else selected_text
        headers["Content-Disposition"] = _attachment_content_disposition(f"{export_title}.txt")
        return Response(content=body.encode("utf-8"), media_type="text/plain; charset=utf-8", headers=headers)

    if format == "compare":
        compare_html = _compare_html(rendered_chapters, risk_signature)
        headers["Content-Disposition"] = _attachment_content_disposition(f"{export_title}.html")
        return Response(content=compare_html.encode("utf-8"), media_type="text/html; charset=utf-8", headers=headers)

    epub_bytes = _build_epub_bytes(
        novel=novel,
        chapter_payloads=rendered_chapters,
        risk_signature=risk_signature,
        novel_dir=request.app.state.artifact_store.novel_dir(novel_id),
    )
    headers["Content-Disposition"] = _attachment_content_disposition(f"{export_title}.epub")
    return Response(content=epub_bytes, media_type="application/epub+zip", headers=headers)


@router.post("/import")
async def import_novel(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    filename = file.filename or "uploaded.txt"
    blob = await file.read()
    result = await import_novel_file(
        db,
        request.app.state.artifact_store,
        filename=filename,
        file_bytes=blob,
    )
    return result.to_response_payload()


@router.delete("/{novel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_novel(novel_id: str, request: Request, db: AsyncSession = Depends(get_db_session)) -> None:
    novel = await db.get(Novel, novel_id)
    if novel is None:
        raise AppError(
            code=ErrorCode.NOT_FOUND,
            message=f"Novel `{novel_id}` not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    await db.delete(novel)
    await db.commit()

    novel_path = request.app.state.artifact_store.novel_dir(novel_id)
    if novel_path.exists():
        shutil.rmtree(novel_path, ignore_errors=True)
