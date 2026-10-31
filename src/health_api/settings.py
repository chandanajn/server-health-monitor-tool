"""Application configuration loaded from environment variables and .env files."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from health_api.core.models import Severity


class Settings(BaseSettings):
    """Application settings with HM_ environment variable prefix."""

    model_config = SettingsConfigDict(
        env_prefix="HM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    root: Path = Field(default=Path("org"), description="Log root directory")
    state_file: Path = Field(default=Path("state/state.json"), description="Checkpoint path")
    interval: float = Field(default=30.0, description="Scanning interval in seconds")
    min_severity: Severity = Field(default=Severity.ERROR, description="Minimum severity threshold")
    api_key: str | None = Field(default=None, description="API key for write endpoints")
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: Literal["text", "json"] = Field(default="text", description="Log output format")
    enable_docs: bool = Field(default=True, description="Enable /docs and /openapi.json")
    enable_metrics: bool = Field(default=True, description="Enable /metrics endpoint")
    host: str = Field(default="127.0.0.1", description="Host to bind for server")
    port: int = Field(default=8000, description="Port to bind for server")

    @field_validator("root", mode="after")
    @classmethod
    def validate_root(cls, v: Path) -> Path:
        resolved = v.resolve()
        if not resolved.exists():
            raise ValueError(f"HM_ROOT directory does not exist: '{v}'")
        if not resolved.is_dir():
            raise ValueError(f"HM_ROOT must be a directory: '{v}'")
        return v

    @field_validator("interval", mode="after")
    @classmethod
    def validate_interval(cls, v: float) -> float:
        if v < 0.1:
            raise ValueError("HM_INTERVAL must be at least 0.1 seconds")
        return v

    @field_validator("min_severity", mode="before")
    @classmethod
    def validate_min_severity(cls, v: object) -> Severity:
        if isinstance(v, Severity):
            return v
        if isinstance(v, str):
            try:
                return Severity.from_name(v)
            except ValueError as exc:
                raise ValueError(f"HM_MIN_SEVERITY invalid: {exc}") from exc
        raise ValueError("HM_MIN_SEVERITY must be a valid severity name")

    @field_validator("api_key", mode="after")
    @classmethod
    def validate_api_key(cls, v: str | None) -> str | None:
        if v is not None and len(v.strip()) < 16:
            raise ValueError("HM_API_KEY must be at least 16 characters long")
        return v

    @field_validator("log_level", mode="after")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        levels = {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"}
        upper = v.strip().upper()
        if upper not in levels:
            raise ValueError(f"HM_LOG_LEVEL invalid: {v!r}")
        return "WARNING" if upper == "WARN" else upper

    @field_validator("port", mode="after")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("HM_PORT must be between 1 and 65535")
        return v

    @model_validator(mode="after")
    def validate_state_outside_root(self) -> Settings:
        root_resolved = self.root.resolve()
        state_resolved = self.state_file.resolve()
        try:
            state_resolved.relative_to(root_resolved)
            raise ValueError(
                f"HM_STATE_FILE '{self.state_file}' cannot be inside HM_ROOT '{self.root}'"
            )
        except ValueError as exc:
            if "cannot be inside" in str(exc):
                raise
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()

