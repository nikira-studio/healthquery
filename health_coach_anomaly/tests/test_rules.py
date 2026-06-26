"""Unit tests for the four default rules.

The tests below cover STA-50 §"Acceptance":

* a known HRV drop produces a fired flag with a context block;
* the detector does not fire when the trend is within threshold;
* when the relevant metric_type is absent, the rule returns
  ``DATA_NOT_AVAILABLE`` and does not fire.

The rules are pure (no HealthQuery calls) so the tests are
fast and deterministic.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from health_coach_anomaly.output import AnomalySeverity, AnomalyStatus
from health_coach_anomaly.rules import (
    RuleContext,
    evaluate_hrv,
    evaluate_rhr,
    evaluate_sleep,
    evaluate_steps,
)
from health_coach_anomaly.thresholds import TunableThresholds
from health_coach_anomaly.windows import WindowSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fixed_window() -> WindowSpec:
    return WindowSpec(
        start=_dt.datetime(2026, 6, 19, tzinfo=_dt.timezone.utc),
        end=_dt.datetime(2026, 6, 26, tzinfo=_dt.timezone.utc),
    )


def _empty_context(illness: bool = False) -> RuleContext:
    return RuleContext(
        rhr_current=[],
        rhr_baseline=[],
        sleep_minutes_recent_week=[],
        sleep_minutes_prior_week=[],
        steps_current_daily=[],
        steps_baseline_daily=[],
        workouts_in_window=[],
        illness_marker_in_window=illness,
    )


# ---------------------------------------------------------------------------
# HRV rule
# ---------------------------------------------------------------------------


class TestHrvRule:
    def test_known_drop_fires_with_context(self):
        """A 30% drop over 7 days vs a 28-day baseline of 60ms must fire."""
        window = _fixed_window()
        baseline = [60.0] * 30  # 30 samples, mean 60ms
        current = [40.0] * 7  # 7 samples, mean 40ms (33% drop)
        result = evaluate_hrv(
            hrv_current=current,
            hrv_baseline=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(illness=False),
        )
        assert result.status == AnomalyStatus.FIRED
        assert result.severity == AnomalySeverity.INFO
        assert result.context["pct_change"] < -TunableThresholds().hrv_drop_pct
        assert result.current_value["mean_ms"] == pytest.approx(40.0)
        assert result.baseline_value["mean_ms"] == pytest.approx(60.0)
        assert "30-day" in result.summary or "baseline" in result.summary

    def test_known_drop_with_illness_marker_promotes_to_prominent(self):
        """An HRV drop with a co-occurring illness marker is rendered prominently."""
        window = _fixed_window()
        baseline = [60.0] * 30
        current = [40.0] * 7
        result = evaluate_hrv(
            hrv_current=current,
            hrv_baseline=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(illness=True),
        )
        assert result.status == AnomalyStatus.FIRED
        assert result.severity == AnomalySeverity.PROMINENT
        assert result.context["illness_marker_in_window"] is True

    def test_within_threshold_does_not_fire(self):
        """A 5% drop is inside the 15% threshold; rule reports within-threshold."""
        window = _fixed_window()
        baseline = [60.0] * 30
        current = [57.0] * 7  # 5% drop
        result = evaluate_hrv(
            hrv_current=current,
            hrv_baseline=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.WITHIN_THRESHOLD
        assert result.severity == AnomalySeverity.INFO

    def test_no_data_does_not_fire(self):
        """When HRV is absent, the rule returns DATA_NOT_AVAILABLE — never a flag."""
        window = _fixed_window()
        result = evaluate_hrv(
            hrv_current=[],
            hrv_baseline=[],
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.DATA_NOT_AVAILABLE
        assert result.severity == AnomalySeverity.INFO
        assert "not yet ingested" in result.summary.lower()
        # Critical: a missing metric never fires a "fired" status.
        assert result.status != AnomalyStatus.FIRED

    def test_insufficient_samples_does_not_fire(self):
        """Two samples in the current window is below the min; not enough signal."""
        window = _fixed_window()
        result = evaluate_hrv(
            hrv_current=[40.0, 41.0],
            hrv_baseline=[60.0] * 30,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.INSUFFICIENT_SAMPLES


# ---------------------------------------------------------------------------
# RHR rule
# ---------------------------------------------------------------------------


class TestRhrRule:
    def test_known_rise_fires(self):
        window = _fixed_window()
        baseline = [60.0] * 30  # 60bpm
        current = [70.0] * 7  # 70bpm = 16.7% rise
        result = evaluate_rhr(
            rhr_current=current,
            rhr_baseline=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.FIRED
        assert result.context["pct_change"] >= TunableThresholds().rhr_rise_pct

    def test_within_threshold_does_not_fire(self):
        window = _fixed_window()
        baseline = [60.0] * 30
        current = [63.0] * 7  # 5% rise
        result = evaluate_rhr(
            rhr_current=current,
            rhr_baseline=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.WITHIN_THRESHOLD

    def test_no_data_returns_data_not_available(self):
        window = _fixed_window()
        result = evaluate_rhr(
            rhr_current=[],
            rhr_baseline=[],
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.DATA_NOT_AVAILABLE
        assert "RHR not yet ingested" in result.summary

    def test_rise_with_illness_promotes_to_prominent(self):
        window = _fixed_window()
        baseline = [60.0] * 30
        current = [70.0] * 7
        result = evaluate_rhr(
            rhr_current=current,
            rhr_baseline=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(illness=True),
        )
        assert result.status == AnomalyStatus.FIRED
        assert result.severity == AnomalySeverity.PROMINENT


# ---------------------------------------------------------------------------
# Sleep rule
# ---------------------------------------------------------------------------


def _night(date: str, hours: float) -> tuple[str, float]:
    return (date, hours * 60.0)


class TestSleepRule:
    def test_week_over_week_drop_fires(self):
        """A 35% drop in total sleep minutes week-over-week fires the rule."""
        window = _fixed_window()
        # recent: 6h × 6 = 36h; prior: 8h × 7 = 56h → 35.7% drop
        recent = [_night(f"2026-06-{d:02d}", 6.0) for d in range(20, 26)]
        prior = [_night(f"2026-06-{d:02d}", 8.0) for d in range(14, 21)]
        result = evaluate_sleep(
            sleep_minutes_recent_week=recent,
            sleep_minutes_prior_week=prior,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.FIRED
        assert result.severity == AnomalySeverity.PROMINENT
        assert result.context["week_drop_fired"] is True
        assert result.context["pct_change"] is not None
        assert result.context["pct_change"] <= -TunableThresholds().sleep_drop_pct

    def test_consecutive_short_nights_fire(self):
        """Four consecutive nights < 6h must fire (3+ threshold)."""
        window = _fixed_window()
        recent = [
            _night("2026-06-20", 5.5),
            _night("2026-06-21", 4.0),
            _night("2026-06-22", 5.0),
            _night("2026-06-23", 5.5),
        ]
        prior = [_night("2026-06-19", 7.5)]
        result = evaluate_sleep(
            sleep_minutes_recent_week=recent,
            sleep_minutes_prior_week=prior,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.FIRED
        assert result.context["consecutive_fired"] is True
        assert result.context["consecutive_short_nights"] == 4

    def test_within_threshold_does_not_fire(self):
        window = _fixed_window()
        recent = [_night(f"2026-06-{d:02d}", 7.0) for d in range(20, 26)]
        prior = [_night(f"2026-06-{d:02d}", 7.5) for d in range(14, 20)]
        result = evaluate_sleep(
            sleep_minutes_recent_week=recent,
            sleep_minutes_prior_week=prior,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.WITHIN_THRESHOLD

    def test_no_sleep_data_does_not_fire(self):
        window = _fixed_window()
        result = evaluate_sleep(
            sleep_minutes_recent_week=[],
            sleep_minutes_prior_week=[],
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.DATA_NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Steps rule
# ---------------------------------------------------------------------------


class TestStepsRule:
    def test_collapse_fires(self):
        """7-day mean steps 30% of baseline (well below 50% threshold) must fire."""
        window = _fixed_window()
        baseline = [(f"2026-05-{d:02d}", 8000.0) for d in range(20, 28)]  # 28-day mean ≈ 8000
        current = [(f"2026-06-{d:02d}", 2400.0) for d in range(20, 26)]  # 7-day mean = 2400 (30%)
        result = evaluate_steps(
            steps_current_daily=current,
            steps_baseline_daily=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.FIRED
        assert result.context["ratio"] < TunableThresholds().steps_collapse_ratio

    def test_within_threshold_does_not_fire(self):
        """7-day mean at 70% of baseline (above 50% threshold) is fine."""
        window = _fixed_window()
        baseline = [(f"2026-05-{d:02d}", 8000.0) for d in range(20, 28)]
        current = [(f"2026-06-{d:02d}", 5600.0) for d in range(20, 26)]  # 70% of baseline
        result = evaluate_steps(
            steps_current_daily=current,
            steps_baseline_daily=baseline,
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.WITHIN_THRESHOLD

    def test_no_data_does_not_fire(self):
        window = _fixed_window()
        result = evaluate_steps(
            steps_current_daily=[],
            steps_baseline_daily=[],
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.DATA_NOT_AVAILABLE

    def test_insufficient_samples_does_not_fire(self):
        """A single day in the current window is below min_current_samples."""
        window = _fixed_window()
        result = evaluate_steps(
            steps_current_daily=[("2026-06-25", 1000.0)],
            steps_baseline_daily=[(f"2026-05-{d:02d}", 8000.0) for d in range(20, 28)],
            window=window,
            thresholds=TunableThresholds(),
            context=_empty_context(),
        )
        assert result.status == AnomalyStatus.INSUFFICIENT_SAMPLES
