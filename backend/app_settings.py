from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    db_path: Path
    ingest_token: str
    read_token: str
    log_level: str
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None
    llm_timeout_seconds: float
    cors_origins: list[str]


def _is_placeholder_token(value: str, placeholder_prefix: str) -> bool:
    return value.startswith(placeholder_prefix)


def get_settings() -> AppSettings:
    db_path = Path(os.getenv("DB_PATH", "data/healthquery.db"))
    ingest_token = os.getenv("HEALTHQUERY_INGEST_TOKEN", "change-me-ingest")
    read_token = os.getenv("HEALTHQUERY_READ_TOKEN", "change-me-read")
    log_level = os.getenv("HEALTHQUERY_LOG_LEVEL", "INFO")
    llm_base_url = os.getenv("HEALTHQUERY_LLM_BASE_URL", "").strip() or None
    llm_model = os.getenv("HEALTHQUERY_LLM_MODEL", "").strip() or None
    llm_api_key = os.getenv("HEALTHQUERY_LLM_API_KEY", "").strip() or None
    llm_timeout_seconds = float(os.getenv("HEALTHQUERY_LLM_TIMEOUT_SECONDS", "60"))
    raw_origins = os.getenv("HEALTHQUERY_CORS_ORIGINS", "").strip()
    cors_origins = [o.strip() for o in raw_origins.split(",") if o.strip()] if raw_origins else ["*"]
    return AppSettings(
        db_path=db_path,
        ingest_token=ingest_token,
        read_token=read_token,
        log_level=log_level,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_timeout_seconds=llm_timeout_seconds,
        cors_origins=cors_origins,
    )


def has_placeholder_ingest_token(settings: AppSettings) -> bool:
    return _is_placeholder_token(settings.ingest_token, "change-me-")


def has_placeholder_read_token(settings: AppSettings) -> bool:
    return _is_placeholder_token(settings.read_token, "change-me-")
