"""Weekly summary generator for the Health Coach.

This package is the read-side analysis layer between HealthQuery (the
upstream SQLite + read API) and the Health Coach's review queue. It is
**not** a generic HealthQuery consumer: every aggregate and rule here is
shaped by the Health Coach's AGENTS.md output bar.

Public entry points
-------------------

* :func:`generate_weekly_summary` — produce a Markdown summary for the
  most recent 7-day window, reading through the
  :mod:`healthquery_client` library. Idempotent against the same
  ``batch_id`` + window: re-running the same inputs yields the same
  output bytes (modulo the run id line).
* :func:`generate_anomaly_investigation` — produce a Markdown anomaly
  card for a single metric, with the context window (other metrics in
  the same range). Used by the STA-50 build ticket.

Design notes
------------

* **Privacy by default.** Aggregates and trend lines only. No raw
  ``metric_points`` rows, no per-stage sleep breakdowns, no raw
  ``payload_json`` blobs reach the Markdown body. The summary is safe
  to paste into a Paperclip comment, a Telegram topic, or a log file.
* **Tolerates absent metrics.** When the live data does not yet carry a
  metric (HRV, body comp, workouts, etc.), the summary prints a
  ``data not available`` note for that trend line rather than
  fabricating a value. This is enforced by :func:`_present_or_note`.
* **Idempotency.** Output bytes are deterministic for the same window
  and source ``batch_id``. The only non-deterministic line is the
  ``run_id`` footer, which the caller passes in explicitly so that the
  heartbeat can stamp it.
* **Auth boundary.** The read token never leaves the client library.
  This package reads ``HEALTHQUERY_READ_TOKEN`` only via the client
  constructor's standard env-var path; it does not import the env var
  itself.
"""

from __future__ import annotations

from .vocabulary import (
    ABSENT_METRIC_TYPES,
    KNOWN_METRIC_TYPES,
    KNOWN_INTERVAL_TYPES,
    KNOWN_SLEEP_STAGES,
    WORKOUT_CODE_TO_LABEL,
    describe_vocabulary,
    label_workout_code,
)
from .analyzer import (
    AnalysisInputs,
    AnomalyFinding,
    MetricAggregate,
    WeeklyTrend,
    compute_weekly_trends,
    compute_wins,
    detect_anomalies,
    next_week_focus,
)
from .report import (
    RenderContext,
    build_render_context,
    build_window_dates,
    deterministic_window_key,
    render_weekly_summary_markdown,
    sha256_batch_id,
)

__all__ = [
    "ABSENT_METRIC_TYPES",
    "AnalysisInputs",
    "AnomalyFinding",
    "KNOWN_INTERVAL_TYPES",
    "KNOWN_METRIC_TYPES",
    "KNOWN_SLEEP_STAGES",
    "MetricAggregate",
    "RenderContext",
    "WeeklyTrend",
    "WORKOUT_CODE_TO_LABEL",
    "build_render_context",
    "build_window_dates",
    "compute_weekly_trends",
    "compute_wins",
    "describe_vocabulary",
    "detect_anomalies",
    "deterministic_window_key",
    "label_workout_code",
    "next_week_focus",
    "render_weekly_summary_markdown",
    "sha256_batch_id",
] 
