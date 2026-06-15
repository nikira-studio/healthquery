from __future__ import annotations

import json
from typing import Any

from db.database import fetch_all, fetch_one


def _build_timeline_event(
    *,
    event_id: str,
    event_time: str,
    start_time: str | None,
    end_time: str | None,
    category: str,
    type_name: str,
    title: str,
    summary: str,
    metrics: dict[str, Any],
    source: str | None,
    record_key: str,
    detail_json: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": event_id,
        "timestamp": event_time,
        "event_id": event_id,
        "event_time": event_time,
        "start_time": start_time,
        "end_time": end_time,
        "category": category,
        "type": type_name,
        "title": title,
        "summary": summary,
        "metrics": metrics,
        "source": source,
        "record_key": record_key,
        "data_quality": "estimated" if source == "fixture" else "measured",
        "detail_json": detail_json,
    }


async def get_overview_view() -> dict[str, Any]:
    summary = await fetch_all(
        """
        SELECT summary_date, steps, active_minutes, sleep_minutes, workouts, updated_at
        FROM daily_summaries
        WHERE COALESCE(steps, 0) > 0
           OR COALESCE(active_minutes, 0) > 0
           OR COALESCE(sleep_minutes, 0) > 0
           OR COALESCE(workouts, 0) > 0
        ORDER BY summary_date DESC
        LIMIT 30
        """
    )
    return {
        "cards": await get_summary_cards(),
        "daily_summaries": summary,
        "sync": await get_sync_status(),
    }


async def get_activity_view() -> dict[str, Any]:
    return {
        "daily_summaries": await fetch_all(
            """
            SELECT summary_date, steps, active_minutes, workouts
            FROM daily_summaries
            ORDER BY summary_date DESC
            LIMIT 30
            """
        ),
        "workouts": await fetch_all(
            """
            SELECT workout_key, activity_type, start_time, end_time, duration_minutes, calories, avg_hr
            FROM workouts
            ORDER BY start_time DESC
            LIMIT 20
            """
        ),
        "interval_metrics": await fetch_all(
            """
            SELECT record_key, metric_type, start_time, end_time, numeric_value, text_value, unit
            FROM metric_intervals
            WHERE metric_type IN ('steps', 'distance', 'active_calories', 'total_calories', 'hydration', 'nutrition', 'mindfulness')
            ORDER BY start_time DESC
            LIMIT 20
            """
        ),
    }


async def get_sleep_view() -> dict[str, Any]:
    return {
        "sessions": await fetch_all(
            """
            SELECT session_key, start_time, end_time, duration_minutes, efficiency_pct
            FROM sleep_sessions
            ORDER BY start_time DESC
            LIMIT 20
            """
        ),
        "stages": await fetch_all(
            """
            SELECT stage_key, session_key, stage_type, start_time, end_time, duration_seconds
            FROM sleep_stages
            ORDER BY start_time DESC
            LIMIT 50
            """
        ),
    }


async def get_vitals_view() -> dict[str, Any]:
    return {
        "point_metrics": await fetch_all(
            """
            SELECT record_key, metric_type, recorded_at, numeric_value, text_value, unit
            FROM metric_points
            WHERE metric_type IN (
              'heart_rate', 'resting_heart_rate', 'heart_rate_variability',
              'oxygen_saturation', 'body_temperature', 'respiratory_rate',
              'blood_pressure', 'blood_glucose'
            )
            ORDER BY recorded_at DESC
            LIMIT 50
            """
        )
    }


async def get_body_view() -> dict[str, Any]:
    return {
        "point_metrics": await fetch_all(
            """
            SELECT record_key, metric_type, recorded_at, numeric_value, text_value, unit
            FROM metric_points
            WHERE metric_type IN ('weight', 'height', 'body_fat', 'lean_body_mass', 'bone_mass', 'body_water_mass')
            ORDER BY recorded_at DESC
            LIMIT 50
            """
        )
    }


async def get_timeline_view(days: int = 14) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    for row in await fetch_all(
        """
        SELECT workout_key, source, start_time, end_time, activity_type, duration_minutes, calories,
               start_time AS event_time, 'activity' AS category, 'Workout recorded' AS title,
               json_object('workout_key', workout_key, 'activity_type', activity_type, 'duration_minutes', duration_minutes, 'calories', calories) AS detail_json
        FROM workouts
        WHERE start_time >= datetime('now', ?)
        ORDER BY start_time DESC
        LIMIT 20
        """,
        (f"-{days} days",),
    ):
        detail = json.loads(row["detail_json"]) if isinstance(row["detail_json"], str) else row["detail_json"]
        events.append(
            _build_timeline_event(
                event_id=f"workout:{row['event_time']}:{row['title']}",
                event_time=row["event_time"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                category=row["category"],
                type_name=row["activity_type"],
                title=row["title"],
                summary=f"{row['activity_type']} workout for {row.get('duration_minutes') or 0} minutes.",
                metrics={
                    "duration_minutes": row.get("duration_minutes"),
                    "calories": row.get("calories"),
                },
                source=row.get("source"),
                record_key=row["workout_key"],
                detail_json=detail,
            )
        )

    for row in await fetch_all(
        """
        SELECT session_key, source, start_time, end_time, duration_minutes, efficiency_pct,
               start_time AS event_time, 'sleep' AS category, 'Sleep session recorded' AS title,
               json_object('session_key', session_key, 'duration_minutes', duration_minutes, 'efficiency_pct', efficiency_pct) AS detail_json
        FROM sleep_sessions
        WHERE start_time >= datetime('now', ?)
        ORDER BY start_time DESC
        LIMIT 20
        """,
        (f"-{days} days",),
    ):
        detail = json.loads(row["detail_json"]) if isinstance(row["detail_json"], str) else row["detail_json"]
        events.append(
            _build_timeline_event(
                event_id=f"sleep:{row['event_time']}:{row['title']}",
                event_time=row["event_time"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                category=row["category"],
                type_name="sleep_session",
                title=row["title"],
                summary=f"Sleep session for {row.get('duration_minutes') or 0} minutes.",
                metrics={
                    "duration_minutes": row.get("duration_minutes"),
                    "efficiency_pct": row.get("efficiency_pct"),
                },
                source=row.get("source"),
                record_key=row["session_key"],
                detail_json=detail,
            )
        )

    for row in await fetch_all(
        """
        SELECT record_key, source, metric_type, recorded_at, numeric_value, text_value, unit,
               recorded_at AS event_time, 'vitals' AS category,
               CASE metric_type
                 WHEN 'heart_rate' THEN 'Heart rate recorded'
                 WHEN 'resting_heart_rate' THEN 'Resting heart rate recorded'
                 WHEN 'heart_rate_variability' THEN 'HRV recorded'
                 WHEN 'oxygen_saturation' THEN 'SpO2 recorded'
                 WHEN 'blood_pressure' THEN 'Blood pressure recorded'
                 ELSE metric_type || ' recorded'
               END AS title,
               json_object('record_key', record_key, 'metric_type', metric_type, 'numeric_value', numeric_value, 'text_value', text_value, 'unit', unit) AS detail_json
        FROM metric_points
        WHERE metric_type IN ('heart_rate', 'resting_heart_rate', 'heart_rate_variability', 'oxygen_saturation', 'blood_pressure')
          AND recorded_at >= datetime('now', ?)
        ORDER BY recorded_at DESC
        LIMIT 30
        """,
        (f"-{days} days",),
    ):
        detail = json.loads(row["detail_json"]) if isinstance(row["detail_json"], str) else row["detail_json"]
        summary = f"{row['metric_type']} recorded"
        if row.get("numeric_value") is not None and row.get("unit"):
            summary = f"{row['metric_type']} {row['numeric_value']} {row['unit']}"
        events.append(
            _build_timeline_event(
                event_id=f"point:{row['event_time']}:{row['title']}",
                event_time=row["event_time"],
                start_time=row["event_time"],
                end_time=None,
                category=row["category"],
                type_name=row["metric_type"],
                title=row["title"],
                summary=summary,
                metrics={
                    "numeric_value": row.get("numeric_value"),
                    "text_value": row.get("text_value"),
                    "unit": row.get("unit"),
                },
                source=row.get("source"),
                record_key=row["record_key"],
                detail_json=detail,
            )
        )

    for row in await fetch_all(
        """
        SELECT summary_date, 'local' AS source, summary_date AS event_time, 'summary' AS category, 'Daily summary updated' AS title,
               json_object('summary_date', summary_date, 'steps', steps, 'active_minutes', active_minutes, 'sleep_minutes', sleep_minutes, 'workouts', workouts) AS detail_json
        FROM daily_summaries
        WHERE summary_date >= date('now', ?)
        ORDER BY summary_date DESC
        LIMIT 14
        """,
        (f"-{days} days",),
    ):
        detail = json.loads(row["detail_json"]) if isinstance(row["detail_json"], str) else row["detail_json"]
        events.append(
            _build_timeline_event(
                event_id=f"summary:{row['event_time']}:{row['title']}",
                event_time=row["event_time"],
                start_time=row["summary_date"],
                end_time=row["summary_date"],
                category=row["category"],
                type_name="daily_summary",
                title=row["title"],
                summary=f"Daily summary for {row['summary_date']}.",
                metrics={
                    "summary_date": row["summary_date"],
                    "steps": detail.get("steps"),
                    "active_minutes": detail.get("active_minutes"),
                    "sleep_minutes": detail.get("sleep_minutes"),
                    "workouts": detail.get("workouts"),
                },
                source=row.get("source"),
                record_key=row["summary_date"],
                detail_json=detail,
            )
        )

    events.sort(key=lambda row: row["event_time"], reverse=True)
    return {
        "days": days,
        "events": events[:200],
    }


async def get_batches_view(limit: int = 10) -> dict[str, Any]:
    batches = await fetch_all(
        """
        SELECT batch_id, source, received_at, processed_count, error_count, status, notes, payload_json
        FROM ingest_batches
        ORDER BY received_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    for batch in batches:
        payload = batch.get("payload_json")
        if isinstance(payload, str):
            try:
                batch["payload_json"] = json.loads(payload)
            except Exception:
                batch["payload_json"] = payload
    return {"batches": batches}


async def get_summary_cards() -> dict[str, Any]:
    daily = await fetch_one(
        """
        SELECT summary_date, steps, active_minutes, sleep_minutes, workouts
        FROM daily_summaries
        WHERE COALESCE(steps, 0) > 0
           OR COALESCE(active_minutes, 0) > 0
           OR COALESCE(sleep_minutes, 0) > 0
           OR COALESCE(workouts, 0) > 0
        ORDER BY summary_date DESC
        LIMIT 1
        """
    )
    return {
        "latest_day": daily,
        "counts": await get_sync_counts(),
    }


async def get_sync_counts() -> dict[str, int]:
    result: dict[str, int] = {}
    for table in ("ingest_batches", "metric_intervals", "metric_points", "sleep_sessions", "sleep_stages", "workouts"):
        row = await fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
        result[table] = int(row["count"]) if row else 0
    return result


async def get_sync_status() -> dict[str, Any]:
    last_sync = await fetch_one(
        "SELECT received_at FROM ingest_batches ORDER BY received_at DESC LIMIT 1"
    )
    return {
        "status": "ok",
        "last_sync_at": last_sync["received_at"] if last_sync else None,
        "counts": await get_sync_counts(),
    }
