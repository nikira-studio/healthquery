from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app_settings import get_settings
from auth import require_read_auth
from db.database import fetch_all
from services.health_views import (
    get_batches_view,
    get_activity_view,
    get_body_view,
    get_overview_view,
    get_sleep_view,
    get_sync_status,
    get_timeline_view,
    get_vitals_view,
)
from services.operational_settings import OperationalSettings, load_operational_settings, save_operational_settings
from services.sql_guard import SqlGuardError, validate_read_only_sql

router = APIRouter(prefix="/api/health", tags=["health"], dependencies=[Depends(require_read_auth)])

MAX_QUERY_ROWS = 1000
MAX_QUERY_BYTES = 1_000_000


class SqlQueryRequest(BaseModel):
    sql: str = Field(..., min_length=1)


def _cap_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, bool]:
    capped_rows: list[dict[str, Any]] = []
    byte_count = len("[]")
    truncated = False
    for row in rows:
        if len(capped_rows) >= MAX_QUERY_ROWS:
            truncated = True
            break
        row_bytes = len(json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        # +1 for comma separator between items
        overhead = 1 if capped_rows else 0
        if byte_count + row_bytes + overhead > MAX_QUERY_BYTES:
            truncated = True
            break
        capped_rows.append(row)
        byte_count += row_bytes + overhead
    if len(capped_rows) < len(rows):
        truncated = True
    return capped_rows, byte_count, truncated


@router.get("/overview", operation_id="get_health_overview")
async def health_overview() -> dict[str, Any]:
    return await get_overview_view()


@router.get("/activity", operation_id="get_health_activity")
async def health_activity() -> dict[str, Any]:
    return await get_activity_view()


@router.get("/sleep", operation_id="get_health_sleep")
async def health_sleep() -> dict[str, Any]:
    return await get_sleep_view()


@router.get("/vitals", operation_id="get_health_vitals")
async def health_vitals() -> dict[str, Any]:
    return await get_vitals_view()


@router.get("/body", operation_id="get_health_body")
async def health_body() -> dict[str, Any]:
    return await get_body_view()


@router.get("/timeline", operation_id="get_health_timeline")
async def health_timeline(days: int = 14) -> dict[str, Any]:
    return await get_timeline_view(days=days)


@router.get("/summary", operation_id="get_health_summary")
async def health_summary() -> dict[str, Any]:
    return await get_overview_view()


@router.get("/status", operation_id="get_health_status")
async def health_status() -> dict[str, Any]:
    return await get_sync_status()


@router.get("/batches", operation_id="get_health_batches")
async def health_batches(limit: int = 10) -> dict[str, Any]:
    return await get_batches_view(limit=limit)


def _config_response(operational: OperationalSettings) -> dict[str, Any]:
    env = get_settings()
    effective_base_url = operational.llm_base_url or env.llm_base_url or ""
    effective_model = operational.llm_model or env.llm_model or ""
    effective_api_key = operational.llm_api_key or env.llm_api_key or ""
    return {
        "stale_sync_threshold_minutes": operational.stale_sync_threshold_minutes,
        "report_window_days": operational.report_window_days,
        "timeline_window_days": operational.timeline_window_days,
        "report_disclaimer": operational.report_disclaimer,
        "llm_enabled": bool(effective_base_url and effective_model),
        "llm_base_url": effective_base_url,
        "llm_model": effective_model or None,
        "llm_api_key_set": bool(effective_api_key),
    }


@router.get("/config", operation_id="get_health_config")
async def health_config() -> dict[str, Any]:
    return _config_response(await load_operational_settings())


class ConfigUpdateRequest(BaseModel):
    stale_sync_threshold_minutes: int | None = None
    report_window_days: int | None = None
    timeline_window_days: int | None = None
    report_disclaimer: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: int | None = None

    @field_validator("stale_sync_threshold_minutes", "report_window_days", "timeline_window_days", "llm_timeout_seconds", mode="before")
    @classmethod
    def positive_int(cls, value: Any) -> Any:
        if value is not None and int(value) < 1:
            raise ValueError("must be a positive integer")
        return value


@router.put("/config", operation_id="put_health_config")
async def update_health_config(body: ConfigUpdateRequest) -> dict[str, Any]:
    current = await load_operational_settings()
    updated = OperationalSettings(
        stale_sync_threshold_minutes=body.stale_sync_threshold_minutes if body.stale_sync_threshold_minutes is not None else current.stale_sync_threshold_minutes,
        report_window_days=body.report_window_days if body.report_window_days is not None else current.report_window_days,
        timeline_window_days=body.timeline_window_days if body.timeline_window_days is not None else current.timeline_window_days,
        report_disclaimer=body.report_disclaimer if body.report_disclaimer is not None else current.report_disclaimer,
        llm_base_url=body.llm_base_url if body.llm_base_url is not None else current.llm_base_url,
        llm_model=body.llm_model if body.llm_model is not None else current.llm_model,
        llm_api_key=body.llm_api_key if body.llm_api_key is not None else current.llm_api_key,
        llm_timeout_seconds=body.llm_timeout_seconds if body.llm_timeout_seconds is not None else current.llm_timeout_seconds,
    )
    await save_operational_settings(updated)
    return _config_response(updated)


@router.post("/query", operation_id="post_health_query")
async def health_query(body: SqlQueryRequest) -> dict[str, Any]:
    try:
        guard = validate_read_only_sql(body.sql)
    except SqlGuardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Fetch one extra row so we can detect truncation without loading an unbounded result set.
    limited_sql = f"SELECT * FROM ({guard.normalized_sql}) LIMIT {MAX_QUERY_ROWS + 1}"
    rows = await fetch_all(limited_sql)
    db_truncated = len(rows) > MAX_QUERY_ROWS
    if db_truncated:
        rows = rows[:MAX_QUERY_ROWS]
    capped_rows, byte_count, byte_truncated = _cap_rows(rows)
    truncated = db_truncated or byte_truncated
    return {
        "sql": guard.normalized_sql,
        "row_count": len(capped_rows),
        "returned_row_count": len(capped_rows),
        "byte_count": byte_count,
        "truncated": truncated,
        "rows": capped_rows,
    }
