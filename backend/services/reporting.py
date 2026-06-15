from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any
from uuid import uuid4

import httpx

from app_settings import get_settings
from db.database import execute, fetch_all, fetch_one
from services.operational_settings import OperationalSettings, load_operational_settings


def _parse_date(value: date | str | None, fallback: date | None = None) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    if fallback is not None:
        return fallback
    return date.today()


def _format_date(value: date) -> str:
    return value.isoformat()


def _daterange(start: date, end: date) -> list[date]:
    current = start
    values: list[date] = []
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(mean(values), 1)


def _trend_label(delta: float | None, units: str) -> str:
    if delta is None:
        return "insufficient data"
    if delta > 0:
        return f"up {delta:.1f} {units}"
    if delta < 0:
        return f"down {abs(delta):.1f} {units}"
    return "flat"


def _latest_by(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: row.get(key) or "")


def _date_bucket(row: dict[str, Any], field: str) -> str:
    value = row.get(field) or ""
    return str(value)[:10]


@dataclass(frozen=True)
class ReportWindow:
    start_date: date
    end_date: date
    settings: OperationalSettings
    latest_summary_date: date | None


async def resolve_report_window(start_date: date | str | None = None, end_date: date | str | None = None) -> ReportWindow:
    settings = await load_operational_settings()
    latest_summary = await fetch_one("SELECT summary_date FROM daily_summaries ORDER BY summary_date DESC LIMIT 1")
    latest_summary_date = date.fromisoformat(latest_summary["summary_date"]) if latest_summary else None
    resolved_end = _parse_date(end_date, fallback=latest_summary_date or date.today())
    resolved_start = _parse_date(start_date, fallback=resolved_end - timedelta(days=settings.report_window_days - 1))
    if resolved_start > resolved_end:
        resolved_start, resolved_end = resolved_end, resolved_start
    return ReportWindow(
        start_date=resolved_start,
        end_date=resolved_end,
        settings=settings,
        latest_summary_date=latest_summary_date,
    )


async def load_report_context(start_date: date | str | None = None, end_date: date | str | None = None) -> dict[str, Any]:
    window = await resolve_report_window(start_date=start_date, end_date=end_date)
    start = _format_date(window.start_date)
    end = _format_date(window.end_date)
    daily_summaries = await fetch_all(
        """
        SELECT summary_date, steps, active_minutes, sleep_minutes, workouts, updated_at
        FROM daily_summaries
        WHERE summary_date BETWEEN ? AND ?
        ORDER BY summary_date ASC
        """,
        (start, end),
    )
    workouts = await fetch_all(
        """
        SELECT workout_key, activity_type, start_time, end_time, duration_minutes, calories, avg_hr
        FROM workouts
        WHERE date(start_time) BETWEEN ? AND ?
        ORDER BY start_time ASC
        """,
        (start, end),
    )
    sleep_sessions = await fetch_all(
        """
        SELECT session_key, start_time, end_time, duration_minutes, efficiency_pct
        FROM sleep_sessions
        WHERE date(start_time) BETWEEN ? AND ?
           OR date(end_time) BETWEEN ? AND ?
        ORDER BY start_time ASC
        """,
        (start, end, start, end),
    )
    point_metrics = await fetch_all(
        """
        SELECT record_key, metric_type, recorded_at, numeric_value, text_value, unit
        FROM metric_points
        WHERE date(recorded_at) BETWEEN ? AND ?
        ORDER BY recorded_at ASC
        """,
        (start, end),
    )
    interval_metrics = await fetch_all(
        """
        SELECT record_key, metric_type, start_time, end_time, numeric_value, text_value, unit
        FROM metric_intervals
        WHERE date(start_time) BETWEEN ? AND ?
        ORDER BY start_time ASC
        """,
        (start, end),
    )
    return {
        "window": window,
        "daily_summaries": daily_summaries,
        "workouts": workouts,
        "sleep_sessions": sleep_sessions,
        "point_metrics": point_metrics,
        "interval_metrics": interval_metrics,
    }


def _step_values(daily_summaries: list[dict[str, Any]]) -> list[float]:
    return [float(row.get("steps") or 0) for row in daily_summaries]


def _step_values_nonzero(daily_summaries: list[dict[str, Any]]) -> list[float]:
    return [v for v in _step_values(daily_summaries) if v > 0]


def _sleep_values(daily_summaries: list[dict[str, Any]]) -> list[float]:
    return [float(row.get("sleep_minutes") or 0) for row in daily_summaries]


def _sleep_values_nonzero(daily_summaries: list[dict[str, Any]]) -> list[float]:
    return [v for v in _sleep_values(daily_summaries) if v > 0]


def _workout_types(workouts: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in workouts:
        result[row["activity_type"]] = result.get(row["activity_type"], 0) + 1
    return dict(sorted(result.items()))


def _point_metric_values(point_metrics: list[dict[str, Any]], metric_type: str) -> list[float]:
    return [float(row["numeric_value"]) for row in point_metrics if row["metric_type"] == metric_type and row.get("numeric_value") is not None]


def _metric_rows(point_metrics: list[dict[str, Any]], metric_type: str) -> list[dict[str, Any]]:
    return [row for row in point_metrics if row["metric_type"] == metric_type]


def _missing_summary_days(start_date: date, end_date: date, daily_summaries: list[dict[str, Any]]) -> list[str]:
    covered = {_date_bucket(row, "summary_date") for row in daily_summaries}
    return [_format_date(day) for day in _daterange(start_date, end_date) if _format_date(day) not in covered]


def _trend_delta(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    midpoint = max(1, len(values) // 2)
    first_half = values[:midpoint]
    second_half = values[midpoint:]
    if not first_half or not second_half:
        return None
    return round((mean(second_half) - mean(first_half)), 1)


def _build_narrative(report: dict[str, Any]) -> str:
    data = report["data"]
    coverage = report["coverage"]
    lines = [
        f"Selected range: {report['start_date']} through {report['end_date']}.",
        f"Coverage: {coverage['summary_days_covered']} summary days, {coverage['sleep_sessions']} sleep sessions, {coverage['workouts']} workouts.",
        f"Steps: {data['steps_total']:,} total, {data['steps_average']:,} average on {data['steps_tracked_days']} days with data (out of {coverage['summary_days_covered']} summary days).",
        f"Sleep: {data['sleep_total_minutes']:,} total minutes, {data['sleep_average_minutes']:,} average on {data['sleep_tracked_days']} nights with data (out of {coverage['summary_days_covered']} summary days).",
    ]
    if data.get("resting_hr_average") is not None:
        lines.append(f"Resting heart rate averaged {data['resting_hr_average']} bpm.")
    if data.get("hrv_average") is not None:
        lines.append(f"HRV averaged {data['hrv_average']} ms.")
    if data.get("latest_weight") is not None:
        weight = data["latest_weight"]
        lines.append(f"Latest weight was {weight['value']} {weight['unit']} on {weight['recorded_at']}.")
    if data.get("latest_body_fat") is not None:
        body_fat = data["latest_body_fat"]
        lines.append(f"Latest body fat was {body_fat['value']} {body_fat['unit']} on {body_fat['recorded_at']}.")
    if coverage["missing_summary_days"]:
        lines.append(f"Missing daily summary coverage on {', '.join(coverage['missing_summary_days'])}.")
    else:
        lines.append("No daily summary gaps were detected in the selected range.")
    lines.append(report["disclaimer"])
    return " ".join(lines)


def _summarize_report(context: dict[str, Any], window: ReportWindow) -> dict[str, Any]:
    daily = context["daily_summaries"]
    workouts = context["workouts"]
    sleep_sessions = context["sleep_sessions"]
    point_metrics = context["point_metrics"]

    steps_values = _step_values(daily)
    steps_nonzero = _step_values_nonzero(daily)
    sleep_values = _sleep_values(daily)
    sleep_nonzero = _sleep_values_nonzero(daily)
    workout_duration_values = [float(row.get("duration_minutes") or 0) for row in workouts]
    resting_hr_values = _point_metric_values(point_metrics, "resting_heart_rate")
    hrv_values = _point_metric_values(point_metrics, "heart_rate_variability")
    weight_rows = _metric_rows(point_metrics, "weight")
    body_fat_rows = _metric_rows(point_metrics, "body_fat")

    summary_days = len(daily)
    steps_tracked_days = len(steps_nonzero)
    sleep_tracked_days = len(sleep_nonzero)
    steps_total = int(sum(steps_values))
    sleep_total_minutes = int(sum(sleep_values))
    active_minutes_total = int(sum(float(row.get("active_minutes") or 0) for row in daily))
    workout_total = len(workouts)

    report = {
        "report_id": f"doctor_visit:{window.start_date.isoformat()}:{window.end_date.isoformat()}:{uuid4().hex[:10]}",
        "report_type": "doctor_visit",
        "start_date": window.start_date.isoformat(),
        "end_date": window.end_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "deterministic",
        "disclaimer": window.settings.report_disclaimer,
        "coverage": {
            "summary_days_covered": summary_days,
            "sleep_sessions": len(sleep_sessions),
            "workouts": workout_total,
            "missing_summary_days": _missing_summary_days(window.start_date, window.end_date, daily),
        },
        "data": {
            "steps_total": steps_total,
            "steps_tracked_days": steps_tracked_days,
            "steps_average": round(steps_total / steps_tracked_days, 1) if steps_tracked_days else 0,
            "steps_peak": max(steps_nonzero) if steps_nonzero else 0,
            "steps_trend": _trend_delta(steps_nonzero),
            "sleep_total_minutes": sleep_total_minutes,
            "sleep_tracked_days": sleep_tracked_days,
            "sleep_average_minutes": round(sleep_total_minutes / sleep_tracked_days, 1) if sleep_tracked_days else 0,
            "sleep_trend": _trend_delta(sleep_nonzero),
            "active_minutes_total": active_minutes_total,
            "workout_count": workout_total,
            "workout_types": _workout_types(workouts),
            "workout_duration_total": int(sum(workout_duration_values)),
            "resting_hr_average": _average(resting_hr_values),
            "hrv_average": _average(hrv_values),
            "latest_weight": _latest_by(
                [
                    {**row, "value": row.get("numeric_value"), "recorded_at": row.get("recorded_at")}
                    for row in weight_rows
                ],
                "recorded_at",
            ),
            "latest_body_fat": _latest_by(
                [
                    {**row, "value": row.get("numeric_value"), "recorded_at": row.get("recorded_at")}
                    for row in body_fat_rows
                ],
                "recorded_at",
            ),
        },
    }

    report["narrative"] = _build_narrative(report)
    report["highlights"] = [
        f"Daily steps averaged {report['data']['steps_average']:,} across {steps_tracked_days} tracked days ({summary_days} total).",
        f"Sleep averaged {report['data']['sleep_average_minutes']:,} minutes on {sleep_tracked_days} nights with data ({summary_days} total).",
        f"Recorded {workout_total} workouts across {len({row['activity_type'] for row in workouts})} activity types.",
    ]
    if report["data"]["resting_hr_average"] is not None:
        report["highlights"].append(f"Resting heart rate averaged {report['data']['resting_hr_average']} bpm.")
    if report["data"]["hrv_average"] is not None:
        report["highlights"].append(f"HRV averaged {report['data']['hrv_average']} ms.")
    report["trend_notes"] = [
        f"Steps trend: {_trend_label(report['data']['steps_trend'], 'steps/day')}.",
        f"Sleep trend: {_trend_label(report['data']['sleep_trend'], 'minutes/day')}.",
    ]
    return report


async def save_report(report: dict[str, Any]) -> None:
    await execute(
        """
        INSERT INTO reports (report_id, report_type, start_date, end_date, content_json, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(report_id) DO UPDATE SET
          report_type = excluded.report_type,
          start_date = excluded.start_date,
          end_date = excluded.end_date,
          content_json = excluded.content_json,
          created_at = CURRENT_TIMESTAMP
        """,
        (
            report["report_id"],
            report["report_type"],
            report["start_date"],
            report["end_date"],
            json.dumps(report, separators=(",", ":"), sort_keys=True),
        ),
    )


async def build_doctor_visit_report(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    window = await resolve_report_window(start_date=start_date, end_date=end_date)
    context = await load_report_context(window.start_date, window.end_date)
    report = _summarize_report(context, window)
    if persist:
        await save_report(report)
    return report


async def _effective_llm_config() -> dict[str, Any]:
    db = await load_operational_settings()
    env = get_settings()
    return {
        "base_url": db.llm_base_url or env.llm_base_url or "",
        "model": db.llm_model or env.llm_model or "",
        "api_key": db.llm_api_key or env.llm_api_key or "",
        "timeout": db.llm_timeout_seconds or env.llm_timeout_seconds or 60,
    }


async def llm_is_configured() -> bool:
    cfg = await _effective_llm_config()
    return bool(cfg["base_url"] and cfg["model"])


async def maybe_rewrite_with_llm(prompt: str) -> str | None:
    cfg = await _effective_llm_config()
    if not cfg["base_url"] or not cfg["model"]:
        return None

    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "Rewrite the provided non-diagnostic health summary clearly and concisely. "
                    "Do not offer medical advice. Preserve the disclaimer and keep the answer grounded in the supplied data."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        # Strip chain-of-thought blocks emitted by reasoning models (DeepSeek, MiniMax, QwQ, etc.)
        import re
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
        return content


async def build_ask_response(question: str, start_date: date | str | None = None, end_date: date | str | None = None) -> dict[str, Any]:
    report = await build_doctor_visit_report(start_date=start_date, end_date=end_date, persist=False)
    question_lower = question.lower().strip()
    evidence: list[str] = []
    answer = report["narrative"]

    if any(term in question_lower for term in ("sleep", "rest")):
        evidence.append(f"Sleep total: {report['data']['sleep_total_minutes']} minutes")
        answer = (
            f"Sleep across the selected range totaled {report['data']['sleep_total_minutes']} minutes "
            f"with an average of {report['data']['sleep_average_minutes']} minutes per day."
        )
    elif any(term in question_lower for term in ("step", "walk", "activity", "workout")):
        evidence.append(f"Steps total: {report['data']['steps_total']}")
        evidence.append(f"Workouts: {report['data']['workout_count']}")
        answer = (
            f"Activity volume was {report['data']['steps_total']:,} steps across the selected range "
            f"with {report['data']['workout_count']} workouts logged."
        )
    elif any(term in question_lower for term in ("hrv", "heart rate", "heart", "pulse")):
        evidence.append(f"Resting HR average: {report['data']['resting_hr_average']}")
        evidence.append(f"HRV average: {report['data']['hrv_average']}")
        answer = (
            f"Resting heart rate averaged {report['data']['resting_hr_average']} bpm and HRV averaged "
            f"{report['data']['hrv_average']} ms in the selected range."
        )
    elif any(term in question_lower for term in ("weight", "body fat", "body", "composition")):
        latest_weight = report["data"].get("latest_weight")
        latest_body_fat = report["data"].get("latest_body_fat")
        if latest_weight:
            evidence.append(f"Weight: {latest_weight['value']} {latest_weight['unit']}")
        if latest_body_fat:
            evidence.append(f"Body fat: {latest_body_fat['value']} {latest_body_fat['unit']}")
        answer = " ".join(
            part
            for part in [
                f"Latest weight was {latest_weight['value']} {latest_weight['unit']}." if latest_weight else "",
                f"Latest body fat was {latest_body_fat['value']} {latest_body_fat['unit']}." if latest_body_fat else "",
            ]
            if part
        ) or answer
    else:
        evidence.append(f"Summary days: {report['coverage']['summary_days_covered']}")
        answer = (
            f"Across {report['coverage']['summary_days_covered']} summary days, the range showed "
            f"{report['data']['steps_total']:,} steps, {report['data']['sleep_total_minutes']:,} minutes of sleep, "
            f"and {report['data']['workout_count']} workouts."
        )

    is_llm = await llm_is_configured()
    if is_llm:
        prompt = (
            f"Question: {question}\n\n"
            f"Health data summary:\n{json.dumps(report, indent=2, sort_keys=True)}\n\n"
            "Draft a concise answer for a personal health dashboard. Keep it non-diagnostic and grounded in the data."
        )
        llm_answer = await maybe_rewrite_with_llm(prompt)
        if llm_answer:
            answer = llm_answer

    return {
        "status": "success",
        "mode": "llm" if is_llm else "deterministic",
        "question": question,
        "answer": answer,
        "evidence": evidence,
        "report": {
            "report_id": report["report_id"],
            "start_date": report["start_date"],
            "end_date": report["end_date"],
            "disclaimer": report["disclaimer"],
        },
    }
