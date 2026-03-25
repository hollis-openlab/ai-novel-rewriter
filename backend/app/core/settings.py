from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_NOVEL_", case_sensitive=False)

    app_name: str = "AI Novel Backend"
    app_version: str = "0.1.0"
    debug: bool = False

    host: str = "0.0.0.0"
    port: int = 8899
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    data_dir: Path = Path("data")
    docs_dir: Path = Path("backend/docs")
    openapi_output_path: Path = Path("backend/docs/openapi.json")
    api_key_encryption_key: str | None = None
    llm_timeout_seconds: float = Field(default=600.0, gt=0)
    rewrite_window_mode_enabled: bool = False
    rewrite_window_mode_guardrail_enabled: bool = True
    rewrite_window_mode_audit_enabled: bool = True
    rewrite_window_mode_novel_allowlist: list[str] = Field(default_factory=list)
    rewrite_window_mode_task_allowlist: list[str] = Field(default_factory=list)

    @property
    def novels_dir(self) -> Path:
        return self.data_dir / "novels"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _normalize_cors_origins(cls, value: object) -> list[str]:
        if value is None:
            return ["http://localhost:5173", "http://127.0.0.1:5173"]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("rewrite_window_mode_novel_allowlist", "rewrite_window_mode_task_allowlist", mode="before")
    @classmethod
    def _normalize_rewrite_allowlists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
