from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db import Novel, StageRun, Task
from backend.app.models.core import FileFormat, StageName, StageStatus

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "gb2312")

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"


@dataclass(slots=True)
class ParsedEpub:
    raw_text: str
    structure: dict[str, Any]
    spine_count: int


@dataclass(slots=True)
class ImportPipelineResult:
    novel_id: str
    task_id: str
    title: str
    total_chars: int
    chapters_detected: int
    file_format: FileFormat
    encoding: str | None = None
    meta_path: str | None = None
    epub_structure_path: str | None = None
    original_file_path: str | None = None

    def to_response_payload(self) -> dict[str, object]:
        return {
            "novel_id": self.novel_id,
            "task_id": self.task_id,
            "title": self.title,
            "total_chars": self.total_chars,
            "chapters_detected": self.chapters_detected,
            "format": self.file_format.value,
        }


class _HTMLTextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "section",
        "tr",
        "td",
        "th",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag == "br" or tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = unescape(data)
        if text:
            self.parts.append(text)

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()


def _decode_text_bytes(data: bytes) -> tuple[str, str]:
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise AppError(
        code=ErrorCode.UNSUPPORTED_FORMAT,
        message="Failed to decode text file using utf-8/gbk/gb2312/gb18030",
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    )


def _normalized_href(base_path: str, href: str) -> str:
    base = PurePosixPath(base_path).parent
    normalized = posixpath.normpath(str(base / href))
    if normalized.startswith("../"):
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message=f"Invalid EPUB path traversal detected: {href}",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    return normalized.lstrip("./")


def _read_xml(zip_file: ZipFile, member: str) -> ET.Element:
    try:
        return ET.fromstring(zip_file.read(member))
    except KeyError as exc:
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message=f"Missing EPUB resource: {member}",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from exc
    except ET.ParseError as exc:
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message=f"Malformed EPUB XML: {member}",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from exc


def _xml_find_text(element: ET.Element, xpath: str, namespace_map: dict[str, str]) -> str | None:
    found = element.find(xpath, namespace_map)
    if found is not None and found.text:
        return found.text.strip()
    return None


def _find_all_text(element: ET.Element, xpath: str, namespace_map: dict[str, str]) -> list[str]:
    values: list[str] = []
    for found in element.findall(xpath, namespace_map):
        if found.text and found.text.strip():
            values.append(found.text.strip())
    return values


def _copy_member(
    zip_file: ZipFile,
    member_path: str,
    destination_root: Path,
    *,
    preferred_name: str | None = None,
) -> str:
    member = PurePosixPath(member_path)
    if preferred_name is None:
        parts = member.parts
    else:
        parts = PurePosixPath(preferred_name).parts
    safe_parts: list[str] = []
    for part in parts:
        if part in {"", ".", ".."}:
            continue
        safe_part = re.sub(r"[^A-Za-z0-9._-]+", "_", part).strip("._") or "asset"
        safe_parts.append(safe_part)
    destination = destination_root.joinpath(*safe_parts) if safe_parts else destination_root / "asset"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(zip_file.read(member_path))
    return str(destination)


def _extract_text_from_html(data: bytes) -> str:
    decoded, _ = _decode_text_bytes(data)
    parser = _HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    return parser.get_text()


def parse_epub_payload(blob: bytes, import_assets_dir: Path) -> ParsedEpub:
    try:
        with ZipFile(BytesIO(blob)) as zip_file:
            container = _read_xml(zip_file, "META-INF/container.xml")
            container_ns = {"c": CONTAINER_NS}
            rootfile = container.find(".//c:rootfile", container_ns)
            if rootfile is None or not rootfile.attrib.get("full-path"):
                raise AppError(
                    code=ErrorCode.UNSUPPORTED_FORMAT,
                    message="EPUB container missing OPF rootfile",
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )

            opf_path = rootfile.attrib["full-path"]
            package = _read_xml(zip_file, opf_path)
            ns = {"opf": OPF_NS, "dc": DC_NS}

            manifest_items: dict[str, dict[str, Any]] = {}
            for item in package.findall("opf:manifest/opf:item", ns):
                item_id = item.attrib.get("id")
                href = item.attrib.get("href")
                if not item_id or not href:
                    continue
                normalized_href = _normalized_href(opf_path, href)
                manifest_items[item_id] = {
                    "href": normalized_href,
                    "media_type": item.attrib.get("media-type", ""),
                    "properties": item.attrib.get("properties", ""),
                }

            spine_refs: list[str] = []
            for itemref in package.findall("opf:spine/opf:itemref", ns):
                idref = itemref.attrib.get("idref")
                if not idref:
                    continue
                manifest = manifest_items.get(idref)
                if manifest is None:
                    continue
                spine_refs.append(manifest["href"])

            metadata = {
                "title": _xml_find_text(package, "opf:metadata/dc:title", ns) or "",
                "author": _xml_find_text(package, "opf:metadata/dc:creator", ns) or "",
                "language": _xml_find_text(package, "opf:metadata/dc:language", ns) or "",
            }

            cover_item_id = None
            meta_cover = package.find("opf:metadata/opf:meta[@name='cover']", ns)
            if meta_cover is not None:
                cover_item_id = meta_cover.attrib.get("content")
            if cover_item_id is None:
                for item_id, item in manifest_items.items():
                    if "cover-image" in item.get("properties", ""):
                        cover_item_id = item_id
                        break

            css_files: list[str] = []
            cover_image: str | None = None
            asset_root = import_assets_dir
            asset_root.mkdir(parents=True, exist_ok=True)

            for item_id, item in manifest_items.items():
                href = item["href"]
                media_type = item["media_type"]
                if media_type == "text/css":
                    css_files.append(_copy_member(zip_file, href, asset_root))
                elif cover_item_id and item_id == cover_item_id and media_type.startswith("image/"):
                    cover_image = _copy_member(zip_file, href, asset_root)

            raw_parts: list[str] = []
            for href in spine_refs:
                try:
                    page_text = _extract_text_from_html(zip_file.read(href))
                except KeyError as exc:
                    raise AppError(
                        code=ErrorCode.UNSUPPORTED_FORMAT,
                        message=f"Missing spine document: {href}",
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    ) from exc
                if page_text:
                    raw_parts.append(page_text)

            raw_text = "\n\n".join(raw_parts).strip()
            structure = {
                "opf_path": opf_path,
                "spine": spine_refs,
                "manifest": manifest_items,
                "metadata": metadata,
                "css_files": css_files,
                "cover_image": cover_image,
            }
            return ParsedEpub(raw_text=raw_text, structure=structure, spine_count=len(spine_refs))
    except BadZipFile as exc:
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message="Invalid EPUB archive",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        ) from exc


async def import_novel_file(
    db: AsyncSession,
    artifact_store: ArtifactStore,
    *,
    filename: str,
    file_bytes: bytes,
) -> ImportPipelineResult:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".epub"}:
        raise AppError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message="Only .txt and .epub uploads are supported",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise AppError(
            code=ErrorCode.FILE_TOO_LARGE,
            message="File exceeds 50MB limit",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            details={"max_bytes": MAX_UPLOAD_BYTES},
        )

    novel_id = str(uuid4())
    task_id = str(uuid4())
    imported_at = datetime.utcnow()
    title = Path(filename).stem
    file_format = FileFormat.TXT if suffix == ".txt" else FileFormat.EPUB

    novel_dir = artifact_store.ensure_novel_dirs(novel_id)
    import_dir = artifact_store.ensure_import_dir(novel_id)
    task_dir = artifact_store.ensure_task_scaffold(novel_id, task_id)
    artifact_store.write_active_task_id(novel_id, task_id)

    epub_structure_path: str | None = None
    chapters_detected = 0
    encoding: str | None = None
    if file_format == FileFormat.TXT:
        raw_text, encoding = _decode_text_bytes(file_bytes)
    else:
        parsed_epub = parse_epub_payload(file_bytes, artifact_store.import_assets_dir(novel_id))
        raw_text = parsed_epub.raw_text
        chapters_detected = parsed_epub.spine_count
        epub_structure_path = str(novel_dir / "epub_structure.json")
        artifact_store.ensure_json(novel_dir / "epub_structure.json", parsed_epub.structure)

    original_file_path = import_dir / filename
    original_file_path.write_bytes(file_bytes)

    raw_path = novel_dir / "raw.txt"
    meta_path = novel_dir / "novel.meta.json"
    raw_path.write_text(raw_text, encoding="utf-8")
    artifact_store.ensure_json(
        meta_path,
        {
            "novel_id": novel_id,
            "task_id": task_id,
            "title": title,
            "original_filename": filename,
            "file_format": file_format.value,
            "file_size": len(file_bytes),
            "total_chars": len(raw_text),
            "chapters_detected": chapters_detected,
            "imported_at": imported_at.isoformat(),
            "encoding": encoding,
        },
    )

    novel_row = Novel(
        id=novel_id,
        title=title,
        original_filename=filename,
        file_format=file_format.value,
        file_size=len(file_bytes),
        total_chars=len(raw_text),
        imported_at=imported_at,
    )
    task_row = Task(
        id=task_id,
        novel_id=novel_id,
        status="active",
        source_task_id=None,
        auto_execute=False,
        artifact_root=str(task_dir),
        created_at=imported_at,
    )
    db.add(novel_row)
    db.add(task_row)
    for stage in StageName:
        is_import = stage == StageName.IMPORT
        db.add(
            StageRun(
                id=str(uuid4()),
                task_id=task_id,
                stage=stage.value,
                run_seq=1,
                status=StageStatus.COMPLETED.value if is_import else StageStatus.PENDING.value,
                started_at=imported_at if is_import else None,
                completed_at=imported_at if is_import else None,
                chapters_total=1 if is_import else 0,
                chapters_done=1 if is_import else 0,
            )
        )
    await db.commit()

    return ImportPipelineResult(
        novel_id=novel_id,
        task_id=task_id,
        title=title,
        total_chars=len(raw_text),
        chapters_detected=chapters_detected,
        file_format=file_format,
        encoding=encoding,
        meta_path=str(meta_path),
        epub_structure_path=epub_structure_path,
        original_file_path=str(original_file_path),
    )
