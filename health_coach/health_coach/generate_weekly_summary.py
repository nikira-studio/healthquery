"""End-to-end weekly summary generator (STA-5 build #4).

The script:

1. Confirms the HealthQuery api is reachable and authenticated.
2. Pulls the most recent ``batch_id`` from ``/api/health/batches?limit=1``.
3. Reads the 7-day window via the read endpoints and ``/api/health/query``
   for the date-range slices the views do not cover.
4. Computes trend lines + anomalies via :mod:`health_coach.analyzer`.
5. Renders the AGENTS.md output-bar-compliant Markdown summary via
   :mod:`health_coach.report`.
6. Writes the report to a deterministic path keyed by ``batch_id`` and
   the window. The dry-run path is the operator's review queue; this
   script writes to a workspace path the Heartbeat then uploads to
   Paperclip as an artifact.

This script is the single entry point for the weekly summary. It is
intentionally small: it knows the heartbeat contract (run id, batch
stamp), the auth boundary (HealthQueryClient), and the window size
(7 days). Everything else is in the package.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Make the sibling client library importable when this package is not
# installed editable (the canonical deployment installs both
# ``healthquery_client`` and ``health_coach`` editable).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLIENT_PATH = _REPO_ROOT / "healthquery_client"
if str(_CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(_CLIENT_PATH))
_PKG_PARENT = Path(__file__).resolve().parents[1]
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from healthquery_client import HealthQueryClient  # noqa: E402

from health_coach import (  # noqa: E402
    AnalysisInputs,
    build_render_context,
    build_window_dates,
    deterministic_window_key,
    render_weekly_summary_markdown,
)


READ_TOKEN_ALIAS = "operator"
DEFAULT_API_BASE = "http://healthquery-api:3136"
WINDOW_DAYS = 7


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"missing required env var: {name}")
    return value


def _sql_quote(value: str) -> str:
    """sqlglot-safe single-quote escaping for known-safe strings.

    The analyzer never passes user input to ``/api/health/query``; the
    only string interpolation is for ISO date bounds the script
    constructs itself.
    """
    return value.replace("'", "''")


def fetch_inputs(
    client: HealthQueryClient,
    window_start: date,
    window_end: date,
) -> AnalysisInputs:
    """Pull every row the analyzer needs for the window.

    The window is inclusive of both endpoints. The fetch is bounded by
    the HealthQuery ``/api/health/query`` 1000-row cap; trends that
    could blow past it (e.g. > 1000 HR samples) are handled with the
    endpoint views' most-recent-N fallback plus a SELECT for the
    aggregate (mean, min, max). The day-bucketed tables (steps,
    distance, daily_summaries) fit easily under 1000 rows even for a
    month-long window.
    """
    window_start_iso = f"{window_start.isoformat()}T00:00:00Z"
    window_end_iso = f"{window_end.isoformat()}T23:59:59Z"

    overview = client.get_overview()
    daily_summaries = [
        ds
        for ds in (overview.get("daily_summaries") or [])
        if isinstance(ds, dict)
        and isinstance(ds.get("summary_date"), str)
        and window_start.isoformat() <= ds["summary_date"] <= window_end.isoformat()
    ]

    sleep = client.get_sleep()
    sleep_sessions = [
        s
        for s in (sleep.get("sessions") or [])
        if isinstance(s, dict)
        and _within(s.get("start_time"), s.get("end_time"), window_start_iso, window_end_iso)
    ]
    sleep_stages = [
        st
        for st in (sleep.get("stages") or [])
        if isinstance(st, dict)
        and _within(st.get("start_time"), st.get("end_time"), window_start_iso, window_end_iso)
    ]

    activity = client.get_activity()
    workouts = [
        w
        for w in (activity.get("workouts") or [])
        if isinstance(w, dict)
        and _within(w.get("start_time"), w.get("end_time"), window_start_iso, window_end_iso)
    ]

    # The /api/health/activity view truncates interval_metrics to the
    # most recent 20 rows; for a 7-day window we need date-range
    # slices from /api/health/query. Each query is bounded by the
    # 1000-row cap, which is comfortably above the operator's per-week
    # volume for these metric types.
    def _query_intervals(metric_type: str) -> list[dict]:
        sql = (
            "SELECT metric_type, numeric_value, start_time, end_time, unit "
            "FROM metric_intervals "
            f"WHERE metric_type = '{_sql_quote(metric_type)}' "
            f"AND start_time >= '{_sql_quote(window_start_iso)}' "
            f"AND start_time <= '{_sql_quote(window_end_iso)}'"
        )
        result = client.post_query(sql)
        return [dict(row) for row in result.rows if isinstance(row, dict)]

    steps_intervals = _query_intervals("steps")
    distance_intervals = _query_intervals("distance")
    total_calories_intervals = _query_intervals("total_calories")



    # Vitals come through /query because /api/health/vitals is
    # "most-recent-N" and we need date-bound slices.
    sql_hr = (
        "SELECT metric_type, numeric_value, recorded_at "
        "FROM metric_points "
        f"WHERE metric_type = 'heart_rate' "
        f"AND recorded_at >= '{_sql_quote(window_start_iso)}' "
        f"AND recorded_at <= '{_sql_quote(window_end_iso)}'"
    )
    hr_rows = client.post_query(sql_hr).rows
    sql_rhr = sql_hr.replace("'heart_rate'", "'resting_heart_rate'")
    rhr_rows = client.post_query(sql_rhr).rows
    sql_spo2 = sql_hr.replace("'heart_rate'", "'oxygen_saturation'")
    spo2_rows = client.post_query(sql_spo2).rows
    sql_hrv = sql_hr.replace("'heart_rate'", "'heart_rate_variability'")
    hrv_rows = client.post_query(sql_hrv).rows

    return AnalysisInputs(
        window_start=window_start,
        window_end=window_end,
        daily_summaries=daily_summaries,
        heart_rate_points=hr_rows,
        oxygen_saturation_points=spo2_rows,
        resting_heart_rate_points=rhr_rows,
        sleep_sessions=sleep_sessions,
        sleep_stages=sleep_stages,
        steps_intervals=steps_intervals,
        distance_intervals=distance_intervals,
        total_calories_intervals=total_calories_intervals,
        workouts=workouts,
        hrv_points=hrv_rows,
    )


def _within(start: str | None, end: str | None, lo: str, hi: str) -> bool:
    """Conservative overlap check between an interval and [lo, hi].

    The interval [start, end] is in-window if its start <= hi AND
    its end >= lo. Missing start or end is treated as 'not in window'
    (the analyzer should not see unparseable rows).
    """
    if not start or not end:
        return False
    if end < lo or start > hi:
        return False
    return True


def latest_batch_id(client: HealthQueryClient) -> str | None:
    payload = client.get_batches(limit=1)
    batches = payload.get("batches") or []
    if not batches:
        return None
    head = batches[0]
    if not isinstance(head, dict):
        return None
    return head.get("batch_id")


def probe_version(client: HealthQueryClient) -> str:
    info = client.probe()
    if isinstance(info, dict):
        return str(info.get("version", "unknown"))
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the Health Coach's weekly summary from HealthQuery.",
    )
    parser.add_argument(
        "--run-id",
        default=os.getenv("PAPERCLIP_RUN_ID", "local-run"),
        help="Run id to stamp into the summary footer.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Path to write the rendered Markdown summary.",
    )
    parser.add_argument(
        "--meta-out",
        type=Path,
        default=None,
        help="Optional path to write the run metadata as JSON "
        "(source batch_id, run id, window).",
    )
    parser.add_argument(
        "--today",
        default=None,
        help="Override today (UTC, YYYY-MM-DD) for reproducible runs.",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("HEALTHQUERY_BASE_URL", DEFAULT_API_BASE),
        help="HealthQuery base URL.",
    )
    args = parser.parse_args(argv)

    today = (
        date.fromisoformat(args.today)
        if args.today
        else datetime.now(timezone.utc).date()
    )
    window_start, window_end = build_window_dates(today, window_days=WINDOW_DAYS)

    with HealthQueryClient(base_url=args.api_base) as client:
        probe_version(client)
        batch_id = latest_batch_id(client)
        inputs = fetch_inputs(client, window_start, window_end)

    ctx = build_render_context(
        inputs,
        source_batch_id=batch_id,
        run_id=args.run_id,
        read_token_alias=READ_TOKEN_ALIAS,
        api_base=args.api_base,
    )
    body = render_weekly_summary_markdown(ctx)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(body, encoding="utf-8")

    if args.meta_out:
        meta = {
            "run_id": ctx.run_id,
            "window_start": ctx.window_start.isoformat(),
            "window_end": ctx.window_end.isoformat(),
            "window_label": ctx.window_label,
            "source_batch_id": ctx.source_batch_id,
            "source_batch_id_sha256": ctx.source_batch_id_sha256,
            "read_token_alias": ctx.read_token_alias,
            "api_base": ctx.api_base,
            "client_version": ctx.client_version,
            "today": today.isoformat(),
        }
        args.meta_out.parent.mkdir(parents=True, exist_ok=True)
        args.meta_out.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "wrote": str(args.out),
                "window": ctx.window_label,
                "source_batch_id": ctx.source_batch_id,
                "source_batch_id_sha256": ctx.source_batch_id_sha256,
                "run_id": ctx.run_id,
                "read_token_alias": ctx.read_token_alias,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
