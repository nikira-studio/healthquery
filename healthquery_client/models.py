"""Typed response models for the HealthQuery read API.

The HealthQuery backend exposes a stable but evolving JSON contract. These
models give callers (especially the Health Coach) explicit, validated
shapes for the endpoints they consume most often, without forcing the
client to construct one model per endpoint.

The models are deliberately permissive on optional fields — HealthQuery
returns ``null`` for missing data, not omissions, so callers can rely on
``key in payload`` checks instead of ``Optional`` everywhere.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _BaseModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class HealthStatus(_BaseModel):
    """Response shape for ``GET /api/health/status``."""

    status: str = Field(..., description="Service liveness marker.")
    last_sync_at: str | None = Field(default=None, description="ISO 8601 last successful ingest.")
    counts: dict[str, int] = Field(default_factory=dict, description="Row counts per table.")


class BatchSummary(_BaseModel):
    """One row in the ``GET /api/health/batches`` response."""

    batch_id: str | None = None
    received_at: str | None = None
    source: str | None = None
    record_count: int | None = None
    payload: dict[str, Any] | None = None


class TimelineEvent(_BaseModel):
    """Stable timeline event schema documented in PRD v1.3.

    Categories in v1: ``activity``, ``sleep``, ``vitals``, ``body``,
    ``workout``, ``nutrition``, ``hydration``, ``mindfulness``, ``system``.
    """

    id: str
    timestamp: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    category: str
    type: str
    title: str
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    record_key: str
    data_quality: str | None = None


class HealthQueryResult(_BaseModel):
    """Response shape for ``POST /api/health/query``.

    The backend caps each response at 1000 rows / 1 MB. ``truncated`` is
    ``True`` whenever the cap fired on either dimension.
    """

    sql: str
    row_count: int
    returned_row_count: int
    byte_count: int
    truncated: bool
    rows: list[dict[str, Any]]


class ConfigResponse(_BaseModel):
    """Response shape for ``GET /api/health/config``."""

    stale_sync_threshold_minutes: int | None = None
    report_window_days: int | None = None
    timeline_window_days: int | None = None
    report_disclaimer: str | None = None
    llm_enabled: bool = False
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key_set: bool = False