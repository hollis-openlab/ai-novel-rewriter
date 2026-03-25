from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import backend.app.api.routes.providers as providers_routes
from backend.app.api.routes.providers import router as providers_router
from backend.app.db import Provider
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session
from backend.app.llm.interface import ConnectionTestResult
from backend.app.models.core import ProviderType


async def _prepare_session(db_path: Path) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _build_app(sessionmaker: async_sessionmaker) -> FastAPI:
    app = FastAPI()
    app.include_router(providers_router)

    async def override_get_db_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app


@pytest.fixture(autouse=True)
def _mock_provider_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_models(
        api_key: str,
        base_url: str,
        *,
        provider_type: ProviderType | str = ProviderType.OPENAI_COMPATIBLE,
        timeout: float = 30.0,
        transport=None,
    ) -> list[str]:
        resolved = provider_type.value if isinstance(provider_type, ProviderType) else str(provider_type)
        if resolved == ProviderType.OPENAI.value:
            return ["gpt-5.4", "gpt-5.4-mini", "gpt-4.1", "gpt-4o-mini"]
        return ["DeepSeek-V3", "DeepSeek-R1", "Qwen2.5-72B-Instruct", "gpt-4o-mini"]

    async def _fake_test_connection(
        api_key: str,
        base_url: str,
        model_name: str,
        *,
        provider_type: ProviderType | str = ProviderType.OPENAI_COMPATIBLE,
        timeout: float = 30.0,
        transport=None,
    ) -> ConnectionTestResult:
        resolved = provider_type if isinstance(provider_type, ProviderType) else ProviderType(str(provider_type))
        return ConnectionTestResult(
            provider_type=resolved,
            model_name=model_name,
            success=True,
            latency_ms=42,
        )

    monkeypatch.setattr(providers_routes, "fetch_provider_models", _fake_fetch_models)
    monkeypatch.setattr(providers_routes, "test_provider_connection", _fake_test_connection)


def test_provider_upsert_same_credentials_updates_in_place(tmp_path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "providers.db")
        try:
            app = _build_app(sessionmaker)
            payload = {
                "name": "OpenAI Compatible",
                "provider_type": "openai_compatible",
                "api_key": "sk-test-1234567890",
                "base_url": "https://example.com/v1/",
                "model_name": "Qwen2.5-32B-Instruct",
                "temperature": 0.7,
                "max_tokens": 4096,
                "rpm_limit": 60,
                "tpm_limit": 100000,
            }

            with TestClient(app) as client:
                first = client.post("/providers", json=payload)
                assert first.status_code == 201
                first_json = first.json()
                assert "api_key" not in first_json
                assert first_json["api_key_masked"].startswith("sk-t")
                assert first_json["provider_type"] == "openai_compatible"
                assert first_json["temperature"] == payload["temperature"]
                assert first_json["max_tokens"] == payload["max_tokens"]

                updated_payload = payload | {
                    "name": "OpenAI Compatible Updated",
                    "model_name": "Qwen2.5-72B-Instruct",
                    "temperature": 0.2,
                    "max_tokens": 8192,
                }
                second = client.post("/providers", json=updated_payload)
                assert second.status_code == 201
                second_json = second.json()

                assert second_json["id"] == first_json["id"]
                assert second_json["model_name"] == "Qwen2.5-72B-Instruct"
                assert second_json["name"] == "OpenAI Compatible Updated"
                assert second_json["temperature"] == 0.2
                assert second_json["max_tokens"] == 8192

                listed = client.get("/providers")
                assert listed.status_code == 200
                listed_json = listed.json()
                assert listed_json["total"] == 1
                assert listed_json["providers"][0]["id"] == first_json["id"]
                assert listed_json["providers"][0]["temperature"] == 0.2
                assert listed_json["providers"][0]["max_tokens"] == 8192

            async with sessionmaker() as session:
                rows = (await session.execute(select(Provider))).scalars().all()
                assert len(rows) == 1
                assert rows[0].model_name == "Qwen2.5-72B-Instruct"
                assert rows[0].provider_type == "openai_compatible"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_fetch_models_and_search_sorted(tmp_path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "providers.db")
        try:
            app = _build_app(sessionmaker)
            draft_payload = {
                "name": "OpenAI",
                "provider_type": "openai",
                "api_key": "sk-openai-123456",
                "base_url": "https://api.openai.com/v1/",
                "model_name": "gpt-4o-mini",
                "temperature": 0.6,
                "max_tokens": 2048,
                "rpm_limit": 60,
                "tpm_limit": 100000,
            }

            with TestClient(app) as client:
                created = client.post("/providers", json=draft_payload)
                assert created.status_code == 201
                provider_id = created.json()["id"]

                draft_fetch = client.post(
                    "/providers/fetch-models",
                    json={
                        "api_key": draft_payload["api_key"],
                        "base_url": draft_payload["base_url"],
                        "provider_type": "openai",
                    },
                )
                assert draft_fetch.status_code == 200
                draft_fetch_json = draft_fetch.json()
                assert draft_fetch_json["source"] == "draft"
                assert draft_fetch_json["provider_id"] is None
                assert "gpt-5.4" in draft_fetch_json["models"]

                saved_fetch = client.post("/providers/fetch-models", json={"provider_id": provider_id})
                assert saved_fetch.status_code == 200
                saved_fetch_json = saved_fetch.json()
                assert saved_fetch_json["source"] == "saved"
                assert saved_fetch_json["provider_id"] == provider_id
                assert "gpt-5.4" in saved_fetch_json["models"]

                search = client.get(f"/providers/{provider_id}/models", params={"q": "gpt-5"})
                assert search.status_code == 200
                search_json = search.json()
                assert search_json["cached"] is True
                assert search_json["models"][:2] == ["gpt-5.4", "gpt-5.4-mini"]

            async with sessionmaker() as session:
                row = (await session.execute(select(Provider).where(Provider.id == provider_id))).scalars().one()
                assert row.model_list_cache_json is not None
                assert row.model_list_fetched_at is not None
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_provider_endpoint_persists_editable_fields(tmp_path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "providers.db")
        try:
            app = _build_app(sessionmaker)
            payload = {
                "name": "Provider A",
                "provider_type": "openai",
                "api_key": "sk-openai-123456",
                "base_url": "https://api.openai.com/v1/",
                "model_name": "gpt-4o-mini",
                "temperature": 0.7,
                "max_tokens": 4096,
                "top_p": 0.9,
                "rpm_limit": 60,
                "tpm_limit": 100000,
            }

            with TestClient(app) as client:
                created = client.post("/providers", json=payload)
                assert created.status_code == 201
                provider_id = created.json()["id"]

                update_payload = {
                    "name": "Provider B",
                    "provider_type": "openai_compatible",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "model_name": "DeepSeek-V3",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                    "top_p": 0.8,
                    "rpm_limit": 120,
                    "tpm_limit": 200000,
                }

                updated = client.put(f"/providers/{provider_id}", json=update_payload)
                assert updated.status_code == 200
                updated_json = updated.json()

                assert updated_json["id"] == provider_id
                assert updated_json["name"] == "Provider B"
                assert updated_json["provider_type"] == "openai_compatible"
                assert updated_json["base_url"] == "https://api.siliconflow.cn/v1"
                assert updated_json["model_name"] == "DeepSeek-V3"
                assert updated_json["temperature"] == 0.3
                assert updated_json["max_tokens"] == 8192
                assert updated_json["top_p"] == 0.8
                assert updated_json["rpm_limit"] == 120
                assert updated_json["tpm_limit"] == 200000

                listed = client.get("/providers")
                assert listed.status_code == 200
                listed_provider = listed.json()["providers"][0]
                assert listed_provider["id"] == provider_id
                assert listed_provider["name"] == "Provider B"
                assert listed_provider["provider_type"] == "openai_compatible"
                assert listed_provider["base_url"] == "https://api.siliconflow.cn/v1"
                assert listed_provider["model_name"] == "DeepSeek-V3"
                assert listed_provider["temperature"] == 0.3
                assert listed_provider["max_tokens"] == 8192
                assert listed_provider["top_p"] == 0.8
                assert listed_provider["rpm_limit"] == 120
                assert listed_provider["tpm_limit"] == 200000

            async with sessionmaker() as session:
                row = await session.get(Provider, provider_id)
                assert row is not None
                assert row.name == "Provider B"
                assert row.provider_type == "openai_compatible"
                assert row.base_url == "https://api.siliconflow.cn/v1"
                assert row.model_name == "DeepSeek-V3"
                assert row.temperature == 0.3
                assert row.max_tokens == 8192
                assert row.top_p == 0.8
                assert row.rpm_limit == 120
                assert row.tpm_limit == 200000
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_connection_endpoints_return_expected_shape(tmp_path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "providers.db")
        try:
            app = _build_app(sessionmaker)
            payload = {
                "name": "OpenAI Compatible",
                "provider_type": "openai_compatible",
                "api_key": "sk-test-abcdef",
                "base_url": "https://example.com/v1/",
                "model_name": "DeepSeek-V3",
                "temperature": 0.7,
                "max_tokens": 4096,
                "rpm_limit": 60,
                "tpm_limit": 100000,
            }

            with TestClient(app) as client:
                created = client.post("/providers", json=payload)
                assert created.status_code == 201
                provider_id = created.json()["id"]

                draft_test = client.post(
                    "/providers/test-connection",
                    json={
                        "provider_type": "openai_compatible",
                        "api_key": payload["api_key"],
                        "base_url": payload["base_url"],
                        "model_name": payload["model_name"],
                    },
                )
                assert draft_test.status_code == 200
                draft_test_json = draft_test.json()
                assert draft_test_json["status"] == "success"
                assert draft_test_json["success"] is True
                assert draft_test_json["latency_ms"] >= 0
                assert draft_test_json["model_name"] == payload["model_name"]
                assert draft_test_json["provider_type"] == "openai_compatible"

                saved_model_test = client.post(
                    "/providers/test-connection",
                    json={
                        "provider_id": provider_id,
                        "model_name": "DeepSeek-R1",
                    },
                )
                assert saved_model_test.status_code == 200
                saved_model_test_json = saved_model_test.json()
                assert saved_model_test_json["status"] == "success"
                assert saved_model_test_json["success"] is True
                assert saved_model_test_json["provider_id"] == provider_id
                assert saved_model_test_json["model_name"] == "DeepSeek-R1"
                assert saved_model_test_json["provider_type"] == "openai_compatible"

                saved_test = client.post(f"/providers/{provider_id}/test")
                assert saved_test.status_code == 200
                saved_test_json = saved_test.json()
                assert saved_test_json["status"] == "success"
                assert saved_test_json["success"] is True
                assert saved_test_json["provider_id"] == provider_id
                assert saved_test_json["model_name"] == payload["model_name"]
                assert saved_test_json["provider_type"] == "openai_compatible"
        finally:
            await engine.dispose()

    asyncio.run(_run())
