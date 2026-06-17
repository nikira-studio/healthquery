from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from db.database import get_connection


POINT_METRICS = {
    "heart_rate",
    "resting_heart_rate",
    "heart_rate_variability",
    "weight",
    "height",
    "blood_pressure",
    "blood_glucose",
    "oxygen_saturation",
    "body_temperature",
    "respiratory_rate",
    "body_fat",
    "lean_body_mass",
    "bone_mass",
    "body_water_mass",
}

INTERVAL_METRICS = {
    "steps",
    "distance",
    "active_calories",
    "total_calories",
    "hydration",
    "nutrition",
    "mindfulness",
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any, fallback: str | None = None) -> str:
    if isinstance(value, str) and value:
        return value
    if fallback:
        return fallback
    return _now_utc()


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _shift_iso_seconds(value: str, seconds: float) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return value
    return (parsed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _parse_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_metric_time(item: dict[str, Any], fallback: str) -> str:
    return _parse_dt(
        item.get("time")
        or item.get("timestamp")
        or item.get("recorded_at")
        or item.get("start_time")
        or item.get("start")
        or item.get("date"),
        fallback=fallback,
    )


def _extract_metric_end_time(item: dict[str, Any]) -> str | None:
    value = (
        item.get("end_time")
        or item.get("end")
        or item.get("finish_time")
        or item.get("session_end_time")
        or item.get("endDateTime")
    )
    return value if isinstance(value, str) and value else None


VALUE_FIELD_BY_METRIC = {
    "heart_rate": ("bpm",),
    "resting_heart_rate": ("bpm",),
    "heart_rate_variability": ("rmssd_ms", "millis", "ms"),
    "oxygen_saturation": ("percentage", "percent"),
    "steps": ("count",),
    "distance": ("meters", "meter", "distance_m"),
    "active_calories": ("calories", "kilocalories"),
    "total_calories": ("calories", "kilocalories"),
    "hydration": ("liters", "milliliters", "volume_ml"),
    "weight": ("kilograms", "kg"),
    "height": ("meters", "centimeters", "cm"),
    "body_fat": ("percentage", "percent"),
}

UNIT_BY_METRIC = {
    "heart_rate": "bpm",
    "resting_heart_rate": "bpm",
    "heart_rate_variability": "ms",
    "oxygen_saturation": "%",
    "steps": "count",
    "distance": "m",
    "active_calories": "kcal",
    "total_calories": "kcal",
    "hydration": "L",
    "weight": "kg",
    "height": "cm",
    "body_fat": "%",
}


def _pick_value_fields(item: dict[str, Any], metric_type: str) -> tuple[float | None, str | None, str | None]:
    numeric_value = None
    for key in ("value", "numeric_value", *VALUE_FIELD_BY_METRIC.get(metric_type, ())):
        if key in item:
            numeric_value = _parse_number(item.get(key))
            if numeric_value is not None:
                break
    text_value = item.get("text_value") or item.get("text")
    unit = item.get("unit") or UNIT_BY_METRIC.get(metric_type)
    return numeric_value, text_value, unit


def _record_key(*parts: str | None) -> str:
    return ":".join("" if part is None else str(part) for part in parts)


def _duration_minutes(item: dict[str, Any]) -> float | None:
    duration = item.get("duration_minutes")
    if duration is not None:
        return _parse_number(duration)
    duration_seconds = item.get("duration_seconds")
    if duration_seconds is not None:
        value = _parse_number(duration_seconds)
        return None if value is None else round(value / 60.0, 2)
    return None


def _extract_sleep_times(item: dict[str, Any], fallback: str) -> tuple[str, str]:
    end_time = _extract_metric_end_time(item)
    explicit_start = (
        item.get("start_time")
        or item.get("session_start_time")
        or item.get("start")
        or item.get("timestamp")
        or item.get("time")
    )
    if isinstance(explicit_start, str) and explicit_start:
        start_time = _parse_dt(explicit_start, fallback=fallback)
        return start_time, end_time or start_time
    if end_time:
        duration_seconds = _parse_number(item.get("duration_seconds"))
        if duration_seconds is not None:
            return _shift_iso_seconds(end_time, -duration_seconds), end_time
        return end_time, end_time
    start_time = _extract_metric_time(item, fallback)
    return start_time, start_time


def _split_blood_pressure(item: dict[str, Any]) -> tuple[float | None, float | None]:
    systolic = item.get("systolic") or item.get("upper")
    diastolic = item.get("diastolic") or item.get("lower")
    return _parse_number(systolic), _parse_number(diastolic)


def _point_key(source: str, metric_type: str, recorded_at: str) -> str:
    return _record_key(source, metric_type, recorded_at)


def _interval_key(source: str, metric_type: str, start_time: str, end_time: str | None) -> str:
    return _record_key(source, metric_type, start_time, end_time)


def _sleep_session_key(source: str, start_time: str, end_time: str) -> str:
    return _record_key(source, "sleep_session", start_time, end_time)


def _sleep_stage_key(session_key: str, stage_type: str, start_time: str, end_time: str) -> str:
    return _record_key(session_key, stage_type, start_time, end_time)


def _workout_key(source: str, activity_type: str, start_time: str, end_time: str, duration: float | None) -> str:
    return _record_key(source, activity_type, start_time, end_time, duration)


def _summary_dates_from_record(start_time: str, end_time: str | None = None) -> set[str]:
    dates = {start_time[:10]}
    if end_time:
        dates.add(end_time[:10])
    return dates


async def ingest_health_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    source = str(payload.get("source") or "health_connect")
    app_version = str(payload.get("app_version") or "")
    batch_id = f"batch_{uuid.uuid4().hex}"
    received_at = _parse_dt(payload.get("timestamp"), fallback=_now_utc())

    processed_count = 0
    error_count = 0
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    errors: list[dict[str, Any]] = []
    touched_dates: set[str] = set()

    conn = await get_connection()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute(
            """
            INSERT INTO ingest_batches (
              batch_id, source, received_at, processed_count, error_count, status, payload_json, notes
            ) VALUES (?, ?, ?, 0, 0, 'processing', ?, ?)
            """,
            (
                batch_id,
                source,
                received_at,
                raw_payload_json,
                app_version or None,
            ),
        )

        async def _existing_key(conn, table: str, column: str, key: str) -> bool:
            async with conn.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
                (key,),
            ) as cursor:
                return await cursor.fetchone() is not None

        async def write_metric_intervals(metric_type: str, items: list[Any]) -> None:
            nonlocal processed_count, error_count, inserted_count, updated_count, skipped_count
            for item in items:
                try:
                    if not isinstance(item, dict):
                        raise ValueError("interval item must be an object")
                    start_time = _extract_metric_time(item, received_at)
                    end_time = _extract_metric_end_time(item)
                    numeric_value, text_value, unit = _pick_value_fields(item, metric_type)
                    record_key = _interval_key(source, metric_type, start_time, end_time)
                    existed = await _existing_key(conn, "metric_intervals", "record_key", record_key)
                    await conn.execute(
                        """
                        INSERT INTO metric_intervals (
                          record_key, batch_id, source, metric_type, start_time, end_time,
                          numeric_value, text_value, unit, raw_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(record_key) DO UPDATE SET
                          batch_id = excluded.batch_id,
                          source = excluded.source,
                          metric_type = excluded.metric_type,
                          start_time = excluded.start_time,
                          end_time = excluded.end_time,
                          numeric_value = excluded.numeric_value,
                          text_value = excluded.text_value,
                          unit = excluded.unit,
                          raw_json = excluded.raw_json,
                          updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            record_key,
                            batch_id,
                            source,
                            metric_type,
                            start_time,
                            end_time,
                            numeric_value,
                            text_value,
                            unit,
                            json.dumps(item, separators=(",", ":"), sort_keys=True),
                        ),
                    )
                    processed_count += 1
                    if existed:
                        updated_count += 1
                    else:
                        inserted_count += 1
                    if metric_type == "steps" and numeric_value is not None:
                        touched_dates.update(_summary_dates_from_record(start_time, end_time))
                except ValueError:
                    skipped_count += 1
                    error_count += 1
                    errors.append({"kind": "interval", "metric_type": metric_type, "reason": "invalid_item"})
                except Exception as exc:
                    error_count += 1
                    errors.append({"kind": "interval", "metric_type": metric_type, "reason": str(exc)})

        async def write_metric_points(metric_type: str, items: list[Any]) -> None:
            nonlocal processed_count, error_count, inserted_count, updated_count, skipped_count
            for item in items:
                try:
                    if not isinstance(item, dict):
                        raise ValueError("point item must be an object")
                    recorded_at = _extract_metric_time(item, received_at)
                    numeric_value, text_value, unit = _pick_value_fields(item, metric_type)
                    if metric_type == "blood_pressure":
                        systolic, diastolic = _split_blood_pressure(item)
                        numeric_value = systolic
                        text_value = json.dumps(
                            {"systolic": systolic, "diastolic": diastolic},
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    record_key = _point_key(source, metric_type, recorded_at)
                    existed = await _existing_key(conn, "metric_points", "record_key", record_key)
                    await conn.execute(
                        """
                        INSERT INTO metric_points (
                          record_key, batch_id, source, metric_type, recorded_at,
                          numeric_value, text_value, unit, raw_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(record_key) DO UPDATE SET
                          batch_id = excluded.batch_id,
                          source = excluded.source,
                          metric_type = excluded.metric_type,
                          recorded_at = excluded.recorded_at,
                          numeric_value = excluded.numeric_value,
                          text_value = excluded.text_value,
                          unit = excluded.unit,
                          raw_json = excluded.raw_json,
                          updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            record_key,
                            batch_id,
                            source,
                            metric_type,
                            recorded_at,
                            numeric_value,
                            text_value,
                            unit,
                            json.dumps(item, separators=(",", ":"), sort_keys=True),
                        ),
                    )
                    processed_count += 1
                    if existed:
                        updated_count += 1
                    else:
                        inserted_count += 1
                    touched_dates.update({recorded_at[:10]})
                except ValueError:
                    skipped_count += 1
                    error_count += 1
                    errors.append({"kind": "point", "metric_type": metric_type, "reason": "invalid_item"})
                except Exception as exc:
                    error_count += 1
                    errors.append({"kind": "point", "metric_type": metric_type, "reason": str(exc)})

        async def write_sleep_sessions(items: list[Any]) -> None:
            nonlocal processed_count, error_count, inserted_count, updated_count, skipped_count
            for item in items:
                try:
                    if not isinstance(item, dict):
                        raise ValueError("sleep item must be an object")
                    start_time, end_time = _extract_sleep_times(item, received_at)
                    session_key = _sleep_session_key(source, start_time, end_time)
                    duration_minutes = _duration_minutes(item)
                    efficiency_pct = _parse_number(item.get("efficiency_pct"))
                    existed = await _existing_key(conn, "sleep_sessions", "session_key", session_key)
                    await conn.execute(
                        """
                        INSERT INTO sleep_sessions (
                          session_key, batch_id, source, start_time, end_time,
                          duration_minutes, efficiency_pct, raw_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(session_key) DO UPDATE SET
                          batch_id = excluded.batch_id,
                          source = excluded.source,
                          start_time = excluded.start_time,
                          end_time = excluded.end_time,
                          duration_minutes = excluded.duration_minutes,
                          efficiency_pct = excluded.efficiency_pct,
                          raw_json = excluded.raw_json,
                          updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            session_key,
                            batch_id,
                            source,
                            start_time,
                            end_time,
                            duration_minutes,
                            efficiency_pct,
                            json.dumps(item, separators=(",", ":"), sort_keys=True),
                        ),
                    )
                    processed_count += 1
                    if existed:
                        updated_count += 1
                    else:
                        inserted_count += 1
                    if duration_minutes is not None:
                        touched_dates.update(_summary_dates_from_record(start_time, end_time))
                    stages = item.get("stages") or item.get("sleep_stages") or []
                    if isinstance(stages, list):
                        for stage in stages:
                            if not isinstance(stage, dict):
                                error_count += 1
                                continue
                            stage_type = str(stage.get("stage_type") or stage.get("stage") or "unknown")
                            stage_start = _extract_metric_time(stage, start_time)
                            stage_end = _extract_metric_end_time(stage) or stage_start
                            stage_key = _sleep_stage_key(session_key, stage_type, stage_start, stage_end)
                            stage_duration = _parse_number(stage.get("duration_seconds"))
                            stage_existed = await _existing_key(conn, "sleep_stages", "stage_key", stage_key)
                            await conn.execute(
                                """
                                INSERT INTO sleep_stages (
                                  stage_key, batch_id, session_key, source, stage_type,
                                  start_time, end_time, duration_seconds, raw_json, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                ON CONFLICT(stage_key) DO UPDATE SET
                                  batch_id = excluded.batch_id,
                                  session_key = excluded.session_key,
                                  source = excluded.source,
                                  stage_type = excluded.stage_type,
                                  start_time = excluded.start_time,
                                  end_time = excluded.end_time,
                                  duration_seconds = excluded.duration_seconds,
                                  raw_json = excluded.raw_json,
                                  updated_at = CURRENT_TIMESTAMP
                                """,
                                (
                                    stage_key,
                                    batch_id,
                                    session_key,
                                    source,
                                    stage_type,
                                    stage_start,
                                    stage_end,
                                    stage_duration,
                                    json.dumps(stage, separators=(",", ":"), sort_keys=True),
                                ),
                            )
                            processed_count += 1
                            if stage_existed:
                                updated_count += 1
                            else:
                                inserted_count += 1
                except ValueError:
                    skipped_count += 1
                    error_count += 1
                    errors.append({"kind": "sleep", "reason": "invalid_item"})
                except Exception as exc:
                    error_count += 1
                    errors.append({"kind": "sleep", "reason": str(exc)})

        async def write_workouts(items: list[Any]) -> None:
            nonlocal processed_count, error_count, inserted_count, updated_count, skipped_count
            for item in items:
                try:
                    if not isinstance(item, dict):
                        raise ValueError("workout item must be an object")
                    activity_type = str(item.get("activity_type") or item.get("type") or "exercise")
                    start_time = _extract_metric_time(item, received_at)
                    end_time = _extract_metric_end_time(item) or start_time
                    duration_minutes = _duration_minutes(item)
                    workout_key = _workout_key(source, activity_type, start_time, end_time, duration_minutes)
                    calories = _parse_number(item.get("calories"))
                    avg_hr = _parse_number(item.get("avg_hr"))
                    existed = await _existing_key(conn, "workouts", "workout_key", workout_key)
                    await conn.execute(
                        """
                        INSERT INTO workouts (
                          workout_key, batch_id, source, activity_type, start_time, end_time,
                          duration_minutes, calories, avg_hr, raw_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(workout_key) DO UPDATE SET
                          batch_id = excluded.batch_id,
                          source = excluded.source,
                          activity_type = excluded.activity_type,
                          start_time = excluded.start_time,
                          end_time = excluded.end_time,
                          duration_minutes = excluded.duration_minutes,
                          calories = excluded.calories,
                          avg_hr = excluded.avg_hr,
                          raw_json = excluded.raw_json,
                          updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            workout_key,
                            batch_id,
                            source,
                            activity_type,
                            start_time,
                            end_time,
                            duration_minutes,
                            calories,
                            avg_hr,
                            json.dumps(item, separators=(",", ":"), sort_keys=True),
                        ),
                    )
                    processed_count += 1
                    if existed:
                        updated_count += 1
                    else:
                        inserted_count += 1
                    touched_dates.update(_summary_dates_from_record(start_time, end_time))
                except ValueError:
                    skipped_count += 1
                    error_count += 1
                    errors.append({"kind": "workout", "reason": "invalid_item"})
                except Exception as exc:
                    error_count += 1
                    errors.append({"kind": "workout", "reason": str(exc)})

        for metric_type in sorted(INTERVAL_METRICS):
            await write_metric_intervals(metric_type, _as_list(payload.get(metric_type)))
        for metric_type in sorted(POINT_METRICS):
            await write_metric_points(metric_type, _as_list(payload.get(metric_type)))
        await write_sleep_sessions(_as_list(payload.get("sleep")))
        await write_workouts(_as_list(payload.get("exercise")))

        for summary_date in sorted(touched_dates):
            async with conn.execute(
                """
                SELECT COALESCE(SUM(numeric_value), 0) AS steps
                FROM metric_intervals
                WHERE metric_type = 'steps' AND substr(start_time, 1, 10) = ?
                """,
                (summary_date,),
            ) as cursor:
                steps_row = await cursor.fetchone()
            async with conn.execute(
                """
                SELECT COALESCE(SUM(duration_minutes), 0) AS sleep_minutes
                FROM sleep_sessions
                WHERE substr(start_time, 1, 10) = ?
                """,
                (summary_date,),
            ) as cursor:
                sleep_row = await cursor.fetchone()
            async with conn.execute(
                """
                SELECT COALESCE(SUM(duration_minutes), 0) AS active_minutes,
                       COUNT(*) AS workouts
                FROM workouts
                WHERE substr(start_time, 1, 10) = ?
                """,
                (summary_date,),
            ) as cursor:
                workout_row = await cursor.fetchone()
            def _int_or_none(value: float | None) -> int | None:
                return int(round(value)) if value is not None else None

            steps_val = _int_or_none(steps_row["steps"] if steps_row else None)
            active_val = _int_or_none(workout_row["active_minutes"] if workout_row else None)
            sleep_val = _int_or_none(sleep_row["sleep_minutes"] if sleep_row else None)
            workouts_val = _int_or_none(workout_row["workouts"] if workout_row else None)

            await conn.execute(
                """
                INSERT INTO daily_summaries (
                  summary_date, steps, active_minutes, sleep_minutes, workouts, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(summary_date) DO UPDATE SET
                  steps = excluded.steps,
                  active_minutes = excluded.active_minutes,
                  sleep_minutes = excluded.sleep_minutes,
                  workouts = excluded.workouts,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (summary_date, steps_val, active_val, sleep_val, workouts_val),
            )

        await conn.execute(
            """
            UPDATE ingest_batches
            SET processed_count = ?, error_count = ?, status = ?
            WHERE batch_id = ?
            """,
            (
                processed_count,
                error_count,
                "completed" if error_count == 0 else "completed_with_errors",
                batch_id,
            ),
        )
        await conn.commit()
        return {
            "batch_id": batch_id,
            "processed": processed_count,
            "inserted": inserted_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "errors": errors,
            "source": source,
        }
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()
