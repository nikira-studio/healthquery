"""Unit tests for the context-window assembler.

The context block is the key differentiator from "raw
deviation only" detectors — every flag's ``context`` dict
should reflect what else moved in the same window.
"""

from __future__ import annotations

import pytest

from health_coach_anomaly.context import (
    AWAKENESS_SPIKE_THRESHOLD,
    aggregate_awake_minutes,
    build_rule_context,
    summarize_context,
)
from health_coach_anomaly.rules import RuleContext
from health_coach_anomaly.windows import WindowSpec

import datetime as _dt


def _fixed_window() -> WindowSpec:
    return WindowSpec(
        start=_dt.datetime(2026, 6, 19, tzinfo=_dt.timezone.utc),
        end=_dt.datetime(2026, 6, 26, tzinfo=_dt.timezone.utc),
    )


def _sleep_session(key: str, duration_minutes: float) -> dict:
    return {
        "session_key": key,
        "start_time": "2026-06-25T22:00:00Z",
        "end_time": "2026-06-26T06:00:00Z",
        "duration_minutes": duration_minutes,
    }


def _awake_stage(session_key: str, duration_seconds: float) -> dict:
    return {
        "session_key": session_key,
        "stage_type": "awake",
        "duration_seconds": duration_seconds,
    }


def _deep_stage(session_key: str, duration_seconds: float) -> dict:
    return {
        "session_key": session_key,
        "stage_type": "deep",
        "duration_seconds": duration_seconds,
    }


class TestAggregateAwakeMinutes:
    def test_sums_awake_stages_per_session(self):
        sessions = [
            _sleep_session("s1", 480.0),
            _sleep_session("s2", 420.0),
        ]
        stages = [
            _awake_stage("s1", 600.0),  # 10 min
            _awake_stage("s1", 300.0),  # 5 min → 15 min total for s1
            _awake_stage("s2", 120.0),  # 2 min for s2
            _deep_stage("s1", 60.0),  # not counted (not awake)
        ]
        annotated = aggregate_awake_minutes(sessions, stages)
        assert annotated[0]["awake_minutes"] == pytest.approx(15.0)
        assert annotated[1]["awake_minutes"] == pytest.approx(2.0)

    def test_session_with_no_awake_stages(self):
        sessions = [_sleep_session("s1", 480.0)]
        stages = [_deep_stage("s1", 60.0)]
        annotated = aggregate_awake_minutes(sessions, stages)
        assert annotated[0]["awake_minutes"] == 0.0

    def test_empty_stages(self):
        sessions = [_sleep_session("s1", 480.0)]
        annotated = aggregate_awake_minutes(sessions, [])
        assert annotated[0]["awake_minutes"] == 0.0


class TestIllnessMarkerDetection:
    def test_illness_flagged_when_awake_pct_above_threshold(self):
        """An awake ratio > 20% of total session minutes flags illness."""
        sessions = [_sleep_session("s1", 480.0)]  # 8h total
        stages = [_awake_stage("s1", 120 * 60)]  # 2h awake (25%)
        annotated = aggregate_awake_minutes(sessions, stages)
        ctx = build_rule_context(
            window=_fixed_window(),
            sleep_sessions=annotated,
            workouts=[],
            rhr_current=[],
            rhr_baseline=[],
            sleep_minutes_recent_week=[],
            sleep_minutes_prior_week=[],
            steps_current_daily=[],
            steps_baseline_daily=[],
        )
        assert ctx.illness_marker_in_window is True

    def test_no_illness_when_awake_pct_below_threshold(self):
        sessions = [_sleep_session("s1", 480.0)]
        stages = [_awake_stage("s1", 60 * 60)]  # 1h awake (12.5%)
        annotated = aggregate_awake_minutes(sessions, stages)
        ctx = build_rule_context(
            window=_fixed_window(),
            sleep_sessions=annotated,
            workouts=[],
            rhr_current=[],
            rhr_baseline=[],
            sleep_minutes_recent_week=[],
            sleep_minutes_prior_week=[],
            steps_current_daily=[],
            steps_baseline_daily=[],
        )
        assert ctx.illness_marker_in_window is False

    def test_no_illness_when_no_awake_stages(self):
        sessions = [_sleep_session("s1", 480.0)]
        stages = [_deep_stage("s1", 60 * 60)]
        annotated = aggregate_awake_minutes(sessions, stages)
        ctx = build_rule_context(
            window=_fixed_window(),
            sleep_sessions=annotated,
            workouts=[],
            rhr_current=[],
            rhr_baseline=[],
            sleep_minutes_recent_week=[],
            sleep_minutes_prior_week=[],
            steps_current_daily=[],
            steps_baseline_daily=[],
        )
        assert ctx.illness_marker_in_window is False

    def test_illness_when_any_session_above_threshold(self):
        """One session above the threshold is enough to flag the window."""
        sessions = [
            _sleep_session("s1", 480.0),  # healthy
            _sleep_session("s2", 480.0),  # ill
        ]
        stages = [
            _awake_stage("s1", 30 * 60),  # 6.25% — healthy
            _awake_stage("s2", 120 * 60),  # 25% — ill
        ]
        annotated = aggregate_awake_minutes(sessions, stages)
        ctx = build_rule_context(
            window=_fixed_window(),
            sleep_sessions=annotated,
            workouts=[],
            rhr_current=[],
            rhr_baseline=[],
            sleep_minutes_recent_week=[],
            sleep_minutes_prior_week=[],
            steps_current_daily=[],
            steps_baseline_daily=[],
        )
        assert ctx.illness_marker_in_window is True


class TestSummarizeContext:
    def test_summarize_returns_serializable_dict(self):
        ctx = RuleContext(
            rhr_current=[65.0, 67.0, 66.0],
            rhr_baseline=[62.0] * 10,
            sleep_minutes_recent_week=[("2026-06-25", 420.0)],
            sleep_minutes_prior_week=[("2026-06-19", 450.0)],
            steps_current_daily=[("2026-06-25", 4000.0)] * 5,
            steps_baseline_daily=[("2026-06-01", 7000.0)] * 10,
            workouts_in_window=[],
            illness_marker_in_window=False,
        )
        summary = summarize_context(ctx, window=_fixed_window())
        assert summary["rhr_samples"] == {"current": 3, "baseline": 10}
        assert summary["sleep_samples"] == {"recent_week": 1, "prior_week": 1}
        assert summary["steps_samples"] == {"current_days": 5, "baseline_days": 10}
        assert summary["workout_count_in_window"] == 0
        assert summary["illness_marker_in_window"] is False
        # The summary must be JSON-serializable (no datetime).
        import json
        json.dumps(summary)
