"""Unit tests for the analyzer.

The analyzer is the deterministic core: it never reads the clock, the
network, or the filesystem. These tests pin the AGENTS.md output bar
behaviors so the renderer's contract is enforceable.
"""

from __future__ import annotations

from datetime import date

import pytest

from health_coach import (
    AnalysisInputs,
    compute_weekly_trends,
    compute_wins,
    detect_anomalies,
    next_week_focus,
)
from health_coach.vocabulary import label_workout_code


def _ds(date_str: str, steps: int | None = None, sleep: int | None = None) -> dict:
    return {
        "summary_date": date_str,
        "steps": steps,
        "active_minutes": None,
        "sleep_minutes": sleep,
        "workouts": None,
        "updated_at": "2026-06-26T00:00:00",
    }


def _hr(when: str, bpm: float = 72.0) -> dict:
    return {"metric_type": "heart_rate", "recorded_at": when, "numeric_value": bpm}


def _rhr(when: str, bpm: float = 60.0) -> dict:
    return {
        "metric_type": "resting_heart_rate",
        "recorded_at": when,
        "numeric_value": bpm,
    }


def _spo2(when: str, pct: float = 97.0) -> dict:
    return {
        "metric_type": "oxygen_saturation",
        "recorded_at": when,
        "numeric_value": pct,
    }


def _hrv(when: str, ms: float = 60.0) -> dict:
    return {
        "metric_type": "heart_rate_variability",
        "recorded_at": when,
        "numeric_value": ms,
    }


def _sleep(start: str, end: str, duration_minutes: float = 420.0) -> dict:
    return {
        "session_key": f"sleep:{start}:{end}",
        "start_time": start,
        "end_time": end,
        "duration_minutes": duration_minutes,
        "efficiency_pct": None,
    }


def _stage(start: str, end: str, stage: str = "light") -> dict:
    return {"stage_type": stage, "start_time": start, "end_time": end, "duration_seconds": 600}


def _steps(when: str, count: int = 1000) -> dict:
    end = _shift_iso(when, 86399)
    return {
        "metric_type": "steps",
        "start_time": when,
        "end_time": end,
        "numeric_value": count,
    }


def _shift_iso(value: str, seconds: int) -> str:
    from datetime import datetime, timedelta
    text = value.replace("Z", "+00:00")
    return (datetime.fromisoformat(text) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _distance(when: str, meters: float = 800.0) -> dict:
    return {
        "metric_type": "distance",
        "start_time": when,
        "end_time": when,
        "numeric_value": meters,
    }


def _workout(start: str, end: str, code: str = "8", minutes: float = 30.0) -> dict:
    return {
        "workout_key": f"w:{start}:{end}",
        "activity_type": code,
        "start_time": start,
        "end_time": end,
        "duration_minutes": minutes,
    }


def test_hrv_data_not_available_when_no_rows() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
    )
    trends = {t.name: t for t in compute_weekly_trends(inputs)}
    assert trends["HRV (heart rate variability)"].data_available is False
    assert "data not available" in trends["HRV (heart rate variability)"].notes[0]


def test_hrv_trend_aggregates_window_and_prior_mean() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        hrv_points=[
            _hrv("2026-06-12T07:00:00Z", 60.0),
            _hrv("2026-06-13T07:00:00Z", 62.0),
            _hrv("2026-06-19T07:00:00Z", 50.0),
            _hrv("2026-06-20T07:00:00Z", 52.0),
            _hrv("2026-06-21T07:00:00Z", 48.0),
        ],
    )
    trends = {t.name: t for t in compute_weekly_trends(inputs)}
    hrv = trends["HRV (heart rate variability)"]
    assert hrv.data_available is True
    window_mean = next(a for a in hrv.aggregates if a.label == "7-day mean")
    prior_mean = next(a for a in hrv.aggregates if a.label == "prior 7-day mean")
    assert window_mean.value == pytest.approx(50.0, abs=0.1)
    assert prior_mean.value == pytest.approx(61.0, abs=0.1)
    assert prior_mean.comparator is not None
    assert prior_mean.comparator < 0  # HRV dropped


def test_hrv_anomaly_fires_when_drop_exceeds_threshold() -> None:
    prior = [_hrv(f"2026-06-{d:02d}T07:00:00Z", 65.0) for d in range(12, 19)]
    inside = [_hrv(f"2026-06-{d:02d}T07:00:00Z", 50.0) for d in range(19, 26)]
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        hrv_points=prior + inside,
        resting_heart_rate_points=[_rhr("2026-06-22T07:00:00Z", 64.0)],
    )
    findings = detect_anomalies(inputs)
    assert any(f.rule == "HRV_DROP_OVER_7D" for f in findings)
    rule = next(f for f in findings if f.rule == "HRV_DROP_OVER_7D")
    assert any("resting heart rate" in ctx for ctx in rule.context_window)


def test_hrv_anomaly_does_not_fire_for_modest_drop() -> None:
    prior = [_hrv(f"2026-06-{d:02d}T07:00:00Z", 60.0) for d in range(12, 19)]
    inside = [_hrv(f"2026-06-{d:02d}T07:00:00Z", 58.0) for d in range(19, 26)]
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        hrv_points=prior + inside,
    )
    findings = detect_anomalies(inputs)
    assert not any(f.rule == "HRV_DROP_OVER_7D" for f in findings)


def test_sleep_collapse_fires_when_session_under_240_minutes() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        sleep_sessions=[
            _sleep("2026-06-20T03:00:00Z", "2026-06-20T06:30:00Z", duration_minutes=210.0),
            _sleep("2026-06-21T03:00:00Z", "2026-06-21T11:00:00Z", duration_minutes=480.0),
        ],
    )
    findings = detect_anomalies(inputs)
    assert any(f.rule == "SLEEP_COLLAPSE" for f in findings)


def test_sleep_collapse_does_not_fire_for_long_sessions() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        sleep_sessions=[
            _sleep("2026-06-20T03:00:00Z", "2026-06-20T11:00:00Z", duration_minutes=480.0),
        ],
    )
    findings = detect_anomalies(inputs)
    assert not any(f.rule == "SLEEP_COLLAPSE" for f in findings)


def test_body_comp_always_data_not_available() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
    )
    trends = {t.name: t for t in compute_weekly_trends(inputs)}
    assert trends["Body composition"].data_available is False


def test_activity_trend_counts_days_above_default_goal() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        steps_intervals=[
            _steps("2026-06-19T23:59:00Z", 8000),
            _steps("2026-06-20T23:59:00Z", 9500),
            _steps("2026-06-21T23:59:00Z", 5000),
            _steps("2026-06-22T23:59:00Z", 7500),
        ],
    )
    trends = {t.name: t for t in compute_weekly_trends(inputs)}
    activity = trends["Activity"]
    assert activity.data_available is True
    assert any("≥ 7000 steps" in n for n in activity.notes)


def test_wins_are_positive_no_shame() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        sleep_sessions=[
            _sleep("2026-06-19T22:00:00Z", "2026-06-20T06:30:00Z"),
            _sleep("2026-06-20T22:00:00Z", "2026-06-21T06:30:00Z"),
            _sleep("2026-06-21T22:00:00Z", "2026-06-22T06:30:00Z"),
            _sleep("2026-06-22T22:00:00Z", "2026-06-23T06:30:00Z"),
            _sleep("2026-06-23T22:00:00Z", "2026-06-24T06:30:00Z"),
        ],
    )
    trends = compute_weekly_trends(inputs)
    wins = compute_wins(inputs, trends)
    joined = " ".join(wins).lower()
    # No shame / coercive language.
    for forbidden in ("should", "must", "fail", "lazy", "missed"):
        assert forbidden not in joined


def test_next_week_focus_defaults_to_maintain() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
        sleep_sessions=[_sleep("2026-06-20T03:00:00Z", "2026-06-20T11:00:00Z")],
        steps_intervals=[_steps("2026-06-20T23:59:00Z", 5000)],
    )
    trends = compute_weekly_trends(inputs)
    findings = detect_anomalies(inputs)
    focus = next_week_focus(trends, findings)
    assert focus.lower().startswith("maintain") or focus.lower().startswith("recovery")


def test_workout_label_mapping() -> None:
    assert label_workout_code("8") == "running"
    assert label_workout_code("79") == "other"
    assert label_workout_code(None) == "unknown"
    assert label_workout_code("12345") == "unknown"


def test_oxygen_saturation_data_not_available_when_no_points() -> None:
    inputs = AnalysisInputs(
        window_start=date(2026, 6, 19),
        window_end=date(2026, 6, 25),
    )
    trends = {t.name: t for t in compute_weekly_trends(inputs)}
    assert trends["Oxygen saturation"].data_available is False
