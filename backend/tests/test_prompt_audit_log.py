from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.api.routes.chapters import router as chapters_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.llm.audit_log import PromptAuditLogger


def _build_app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(chapters_router)
    app.state.artifact_store = ArtifactStore(tmp_path / "data")

    @app.exception_handler(AppError)
    async def _handle_app_error(_, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, **exc.details),
        )

    return app


def test_prompt_audit_logger_writes_jsonl_per_chapter(tmp_path: Path) -> None:
    logger = PromptAuditLogger(base_dir=tmp_path / "prompt-audit")

    entry = logger.record_call(
        novel_id="novel-001",
        chapter_index=12,
        stage="analyze",
        system_prompt="system prompt",
        user_prompt="user prompt",
        params={"temperature": 0.7, "max_tokens": 2048},
        provider="openai_compatible",
        model_name="gpt-4o-mini",
        response={"choices": [{"message": {"content": "ok"}}]},
        usage={"prompt_tokens": 12, "completion_tokens": 8},
        validation={"passed": True},
        duration_ms=321,
        attempt=2,
        call_id="call-123",
    )

    path = logger.chapter_path("novel-001", 12)
    assert path.exists()
    assert entry.call_id == "call-123"

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["call_id"] == "call-123"
    assert payload["chapter_index"] == 12
    assert payload["provider"] == "openai_compatible"
    assert payload["usage"]["prompt_tokens"] == 12
    assert payload["validation"]["passed"] is True

    logger.record_call(
        novel_id="novel-001",
        chapter_index=12,
        stage="rewrite",
        system_prompt="system prompt",
        user_prompt="user prompt",
        params={"temperature": 0.5},
        provider="openai",
        response="done",
        duration_ms=120,
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_prompt_logs_api_reads_jsonl_and_returns_degraded_retry(tmp_path: Path) -> None:
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        logger = PromptAuditLogger()
        first_entry = logger.record_call(
            novel_id="novel-001",
            chapter_index=3,
            stage="analyze",
            system_prompt="system prompt",
            user_prompt="user prompt",
            params={"temperature": 0.7},
            provider="openai_compatible",
            model_name="gpt-4o-mini",
            response="analysis result",
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            validation={"passed": True, "details": {"summary_chars": 128}},
            duration_ms=240,
            attempt=1,
            call_id="call-001",
        )
        second_entry = logger.record_call(
            novel_id="novel-001",
            chapter_index=3,
            stage="analyze",
            system_prompt="system prompt 2",
            user_prompt="user prompt 2",
            params={"temperature": 0.6},
            provider="openai_compatible",
            model_name="gpt-4o-mini",
            response="analysis result 2",
            usage={"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
            validation={"passed": True, "details": {"summary_chars": 96}},
            duration_ms=180,
            attempt=1,
            call_id="call-002",
        )

        app = _build_app(tmp_path)

        with TestClient(app) as client:
            empty = client.get("/novels/novel-001/chapters/4/prompt-logs")
            assert empty.status_code == 200
            assert empty.json() == {
                "novel_id": "novel-001",
                "chapter_idx": 4,
                "total": 0,
                "data": [],
            }

            response = client.get("/novels/novel-001/chapters/3/prompt-logs")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            item = payload["data"][0]
            assert item["call_id"] == second_entry.call_id
            assert item["stage"] == "analyze"
            assert payload["data"][1]["call_id"] == first_entry.call_id
            assert item["tokens"] == {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}
            assert item["validation"]["passed"] is True
            assert item["validation"]["details"]["summary_chars"] == 96

            retry = client.post(f"/novels/novel-001/chapters/3/prompt-logs/{first_entry.call_id}/retry")
            assert retry.status_code == 200
            retry_json = retry.json()
            assert retry_json["status"] == "queued"
            assert retry_json["replay_mode"] == "degraded"
            assert retry_json["stage"] == "analyze"
            assert "degraded mode" in retry_json["message"]
    finally:
        os.chdir(cwd)
