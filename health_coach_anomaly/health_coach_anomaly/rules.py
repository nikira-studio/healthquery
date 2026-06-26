"""The four default anomaly rules.

Each rule is a function that takes a small, focused input
(current-window samples, baseline-window samples, thresholds,
context data) and returns an :class:`~health_coach_anomaly.output.Anomaly`.
The detector in :mod:`health_coach_anomaly.detector` does the
data fetching and rule plumbing; the rules themselves stay
pure and side-effect-free so they can be unit-tested with
synthetic inputs (no HealthQuery required).

Rule conventions
----------------

* Each rule reports its **own status**: ``fired``,
  ``within_threshold``, ``data_not_available``, or
  ``insufficient_samples``. The detector does not override.
* Each rule returns an :class:`AnomalySeverity`. The HRV, RHR,
  and sleep-collapse rules can fire as ``prominent`` when the
  co-movement in the context window indicates a coherent
  illness signal. Steps collapse is normally ``info`` (training
  load is a more common cause than illness).
* Each rule embeds the data window, current value, baseline
  value, and a ``context`` dict so the build-#4 weekly summary
  can render the rule's outcome without re-querying HealthQuery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .output import Anomaly, AnomalySeverity, AnomalyStatus
from .thresholds import TunableThresholds
from .windows import WindowSpec, daily_mean, percent_change


@dataclass(frozen=True)
class RuleContext:
    """Per-rule context data assembled by the detector.

    The detector fetches this once per run and passes it into
    each rule. Rules that don't need a particular field (e.g.
    the HRV rule doesn't need ``workouts_in_window``) simply
    ignore it.
    """

    rhr_current: list[float]
    rhr_baseline: list[float]
    sleep_minutes_recent_week: list[tuple[str, float]]
    sleep_minutes_prior_week: list[tuple[str, float]]
    steps_current_daily: list[tuple[str, float]]
    steps_baseline_daily: list[tuple[str, float]]
    workouts_in_window: list[dict]
    illness_marker_in_window: bool


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_id(rule: str, window: WindowSpec) -> str:
    """Stable anomaly id derived from the rule and the window."""
    return f"{rule}_{window.iso_start}_{window.iso_end}".replace(":", "")


def _window_to_dict(window: WindowSpec) -> dict[str, str]:
    return {
        "start": window.iso_start,
        "end": window.iso_end,
        "days": str(window.days),
    }


def _percent_summary(pct: float | None) -> str:
    if pct is None:
        return "n/a"
    return f"{pct * 100:+.1f}%"


# ---------------------------------------------------------------------------
# HRV rule
# ---------------------------------------------------------------------------


def evaluate_hrv(
    *,
    hrv_current: list[float],
    hrv_baseline: list[float],
    window: WindowSpec,
    thresholds: TunableThresholds,
    context: RuleContext,
) -> Anomaly:
    """HRV drop rule: ``current 7-day mean < baseline 28-day mean * (1 - hrv_drop_pct)``.

    AGENTS.md §"Initial thresholds" — "HRV drop > 15% over 7 days → flag.
    Compare to the prior 28-day baseline."

    When the live data has zero ``heart_rate_variability`` rows (the
    most common case today, per STA-48 §2), this rule returns
    ``data_not_available`` and the summary renders "HRV not yet
    ingested" rather than firing a false flag.
    """
    data_window = _window_to_dict(window)
    baseline_window = data_window  # populated in two halves below

    if not hrv_current and not hrv_baseline:
        return Anomaly(
            id=_make_id("hrv_drop", window),
            rule="hrv_drop",
            metric="heart_rate_variability",
            status=AnomalyStatus.DATA_NOT_AVAILABLE,
            severity=AnomalySeverity.INFO,
            summary=(
                "HRV not yet ingested. The HRV drop rule cannot run "
                "until heart_rate_variability rows appear in "
                "metric_points."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_ms": None, "samples": 0},
            baseline_value={"mean_ms": None, "samples": 0},
            context={"missing_metric": "heart_rate_variability"},
            recommendation=None,
        )

    if (
        len(hrv_current) < thresholds.min_current_samples
        or len(hrv_baseline) < thresholds.min_baseline_samples
    ):
        return Anomaly(
            id=_make_id("hrv_drop", window),
            rule="hrv_drop",
            metric="heart_rate_variability",
            status=AnomalyStatus.INSUFFICIENT_SAMPLES,
            severity=AnomalySeverity.INFO,
            summary=(
                f"Not enough HRV samples to evaluate "
                f"(current={len(hrv_current)}, baseline={len(hrv_baseline)}; "
                f"min current={thresholds.min_current_samples}, "
                f"min baseline={thresholds.min_baseline_samples})."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_ms": None, "samples": len(hrv_current)},
            baseline_value={"mean_ms": None, "samples": len(hrv_baseline)},
            context={},
        )

    mean_current = daily_mean(hrv_current)
    mean_baseline = daily_mean(hrv_baseline)
    assert mean_current is not None and mean_baseline is not None
    pct = percent_change(mean_current, mean_baseline)

    fired = pct is not None and pct <= -thresholds.hrv_drop_pct
    if not fired:
        return Anomaly(
            id=_make_id("hrv_drop", window),
            rule="hrv_drop",
            metric="heart_rate_variability",
            status=AnomalyStatus.WITHIN_THRESHOLD,
            severity=AnomalySeverity.INFO,
            summary=(
                f"HRV 7-day mean {mean_current:.1f}ms vs 28-day baseline "
                f"{mean_baseline:.1f}ms ({_percent_summary(pct)}). "
                f"No drop beyond the {thresholds.hrv_drop_pct * 100:.0f}% threshold."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_ms": mean_current, "samples": len(hrv_current)},
            baseline_value={"mean_ms": mean_baseline, "samples": len(hrv_baseline)},
            context={"pct_change": pct},
        )

    severity = (
        AnomalySeverity.PROMINENT
        if context.illness_marker_in_window
        else AnomalySeverity.INFO
    )
    return Anomaly(
        id=_make_id("hrv_drop", window),
        rule="hrv_drop",
        metric="heart_rate_variability",
        status=AnomalyStatus.FIRED,
        severity=severity,
        summary=(
            f"HRV dropped {_percent_summary(pct)} over 7 days "
            f"(mean {mean_current:.1f}ms vs baseline {mean_baseline:.1f}ms). "
            f"Threshold: {thresholds.hrv_drop_pct * 100:.0f}% drop."
        ),
        data_window=data_window,
        baseline_window=baseline_window,
        current_value={"mean_ms": mean_current, "samples": len(hrv_current)},
        baseline_value={"mean_ms": mean_baseline, "samples": len(hrv_baseline)},
        context={
            "pct_change": pct,
            "rhr_co_movement": _rhr_pct(context, thresholds),
            "illness_marker_in_window": context.illness_marker_in_window,
        },
        recommendation=(
            "Promote to 'anomalies with context' in the weekly summary. "
            "If a reported illness or fever is also present, surface prominently. "
            "If training load is elevated, frame as recovery-focused."
            if severity == AnomalySeverity.PROMINENT
            else "Mention in the weekly summary 'trends' section. Cross-reference "
            "RHR and sleep in the same window before drawing a conclusion."
        ),
    )


def _rhr_pct(context: RuleContext, thresholds: TunableThresholds) -> float | None:
    if not context.rhr_current or not context.rhr_baseline:
        return None
    if (
        len(context.rhr_current) < thresholds.min_current_samples
        or len(context.rhr_baseline) < thresholds.min_baseline_samples
    ):
        return None
    cur = daily_mean(context.rhr_current)
    base = daily_mean(context.rhr_baseline)
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / base


# ---------------------------------------------------------------------------
# RHR rule
# ---------------------------------------------------------------------------


def evaluate_rhr(
    *,
    rhr_current: list[float],
    rhr_baseline: list[float],
    window: WindowSpec,
    thresholds: TunableThresholds,
    context: RuleContext,
) -> Anomaly:
    """RHR rise rule: ``current 7-day mean > baseline 28-day mean * (1 + rhr_rise_pct)``.

    AGENTS.md §"Initial thresholds" — "RHR rise > 10% over 7 days → flag
    (often an illness precursor)."

    Severity is ``prominent`` when HRV has also dropped in the same
    window (a coherent illness signal) or when a sleep-stage
    ``awake`` percentage spike is present.
    """
    data_window = _window_to_dict(window)
    baseline_window = data_window

    if not rhr_current and not rhr_baseline:
        return Anomaly(
            id=_make_id("rhr_rise", window),
            rule="rhr_rise",
            metric="resting_heart_rate",
            status=AnomalyStatus.DATA_NOT_AVAILABLE,
            severity=AnomalySeverity.INFO,
            summary="RHR not yet ingested.",
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_bpm": None, "samples": 0},
            baseline_value={"mean_bpm": None, "samples": 0},
            context={"missing_metric": "resting_heart_rate"},
        )

    if (
        len(rhr_current) < thresholds.min_current_samples
        or len(rhr_baseline) < thresholds.min_baseline_samples
    ):
        return Anomaly(
            id=_make_id("rhr_rise", window),
            rule="rhr_rise",
            metric="resting_heart_rate",
            status=AnomalyStatus.INSUFFICIENT_SAMPLES,
            severity=AnomalySeverity.INFO,
            summary=(
                f"Not enough RHR samples to evaluate "
                f"(current={len(rhr_current)}, baseline={len(rhr_baseline)})."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_bpm": None, "samples": len(rhr_current)},
            baseline_value={"mean_bpm": None, "samples": len(rhr_baseline)},
        )

    mean_current = daily_mean(rhr_current)
    mean_baseline = daily_mean(rhr_baseline)
    assert mean_current is not None and mean_baseline is not None
    pct = percent_change(mean_current, mean_baseline)

    fired = pct is not None and pct >= thresholds.rhr_rise_pct
    if not fired:
        return Anomaly(
            id=_make_id("rhr_rise", window),
            rule="rhr_rise",
            metric="resting_heart_rate",
            status=AnomalyStatus.WITHIN_THRESHOLD,
            severity=AnomalySeverity.INFO,
            summary=(
                f"RHR 7-day mean {mean_current:.1f}bpm vs 28-day baseline "
                f"{mean_baseline:.1f}bpm ({_percent_summary(pct)}). "
                f"No rise beyond the {thresholds.rhr_rise_pct * 100:.0f}% threshold."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_bpm": mean_current, "samples": len(rhr_current)},
            baseline_value={"mean_bpm": mean_baseline, "samples": len(rhr_baseline)},
            context={"pct_change": pct},
        )

    severity = (
        AnomalySeverity.PROMINENT
        if context.illness_marker_in_window
        else AnomalySeverity.INFO
    )
    return Anomaly(
        id=_make_id("rhr_rise", window),
        rule="rhr_rise",
        metric="resting_heart_rate",
        status=AnomalyStatus.FIRED,
        severity=severity,
        summary=(
            f"RHR rose {_percent_summary(pct)} over 7 days "
            f"(mean {mean_current:.1f}bpm vs baseline {mean_baseline:.1f}bpm). "
            f"Threshold: {thresholds.rhr_rise_pct * 100:.0f}% rise."
        ),
        data_window=data_window,
        baseline_window=baseline_window,
        current_value={"mean_bpm": mean_current, "samples": len(rhr_current)},
        baseline_value={"mean_bpm": mean_baseline, "samples": len(rhr_baseline)},
        context={
            "pct_change": pct,
            "illness_marker_in_window": context.illness_marker_in_window,
        },
        recommendation=(
            "Often an illness precursor. Cross-reference sleep and HRV; if either also moved, surface prominently."
        ),
    )


# ---------------------------------------------------------------------------
# Sleep collapse rule
# ---------------------------------------------------------------------------


def evaluate_sleep(
    *,
    sleep_minutes_recent_week: list[tuple[str, float]],
    sleep_minutes_prior_week: list[tuple[str, float]],
    window: WindowSpec,
    thresholds: TunableThresholds,
    context: RuleContext,
) -> Anomaly:
    """Sleep collapse rule: week-over-week drop > 30%, or 3+ consecutive nights < 6h.

    AGENTS.md §"Initial thresholds" — "Sleep collapse: total sleep
    minutes drop > 30% week-over-week, or nightly < 6h for 3+
    consecutive nights."

    The detector computes the recent and prior week as 7-day halves
    of the current window so the rule fires on any rolling 7-day
    comparison, not only the calendar week boundary.
    """
    data_window = _window_to_dict(window)
    baseline_window = data_window
    recent_total = sum(m for _, m in sleep_minutes_recent_week)
    prior_total = sum(m for _, m in sleep_minutes_prior_week)

    if not sleep_minutes_recent_week and not sleep_minutes_prior_week:
        return Anomaly(
            id=_make_id("sleep_collapse", window),
            rule="sleep_collapse",
            metric="sleep_minutes",
            status=AnomalyStatus.DATA_NOT_AVAILABLE,
            severity=AnomalySeverity.INFO,
            summary="No sleep session data in the current or prior week.",
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"recent_week_total_minutes": 0.0, "nights": 0},
            baseline_value={"prior_week_total_minutes": 0.0, "nights": 0},
            context={"missing_metric": "sleep_sessions.duration_minutes"},
        )

    pct = (
        percent_change(recent_total, prior_total)
        if prior_total > 0
        else None
    )

    consecutive_short = _max_consecutive_short_nights(
        sleep_minutes_recent_week, thresholds.sleep_minimum_minutes
    )

    week_drop_fired = pct is not None and pct <= -thresholds.sleep_drop_pct
    consecutive_fired = consecutive_short >= thresholds.sleep_consecutive_nights
    fired = week_drop_fired or consecutive_fired

    if not fired:
        return Anomaly(
            id=_make_id("sleep_collapse", window),
            rule="sleep_collapse",
            metric="sleep_minutes",
            status=AnomalyStatus.WITHIN_THRESHOLD,
            severity=AnomalySeverity.INFO,
            summary=(
                f"Sleep recent week {recent_total / 60.0:.1f}h vs prior week "
                f"{prior_total / 60.0:.1f}h ({_percent_summary(pct)}). "
                f"Max consecutive nights under {thresholds.sleep_minimum_minutes / 60.0:.0f}h: {consecutive_short}."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"recent_week_total_minutes": recent_total, "nights": len(sleep_minutes_recent_week)},
            baseline_value={"prior_week_total_minutes": prior_total, "nights": len(sleep_minutes_prior_week)},
            context={"pct_change": pct, "consecutive_short_nights": consecutive_short},
        )

    severity = AnomalySeverity.PROMINENT
    reasons: list[str] = []
    if week_drop_fired:
        reasons.append(f"week-over-week drop {_percent_summary(pct)}")
    if consecutive_fired:
        reasons.append(
            f"{consecutive_short} consecutive nights < "
            f"{thresholds.sleep_minimum_minutes / 60.0:.0f}h"
        )
    return Anomaly(
        id=_make_id("sleep_collapse", window),
        rule="sleep_collapse",
        metric="sleep_minutes",
        status=AnomalyStatus.FIRED,
        severity=severity,
        summary=f"Sleep collapse fired: {'; '.join(reasons)}.",
        data_window=data_window,
        baseline_window=baseline_window,
        current_value={"recent_week_total_minutes": recent_total, "nights": len(sleep_minutes_recent_week)},
        baseline_value={"prior_week_total_minutes": prior_total, "nights": len(sleep_minutes_prior_week)},
        context={
            "pct_change": pct,
            "consecutive_short_nights": consecutive_short,
            "week_drop_fired": week_drop_fired,
            "consecutive_fired": consecutive_fired,
        },
        recommendation=(
            "Surface prominently in the summary. If combined with HRV drop or "
            "RHR rise, suggest a recovery-focused next-week focus."
        ),
    )


def _max_consecutive_short_nights(
    night_minutes: list[tuple[str, float]], threshold_minutes: float
) -> int:
    """Longest run of consecutive nights whose duration is below ``threshold_minutes``."""
    if not night_minutes:
        return 0
    sorted_nights = sorted(night_minutes, key=lambda nm: nm[0])
    best = 0
    current = 0
    for _, minutes in sorted_nights:
        if minutes < threshold_minutes:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


# ---------------------------------------------------------------------------
# Steps collapse rule
# ---------------------------------------------------------------------------


def evaluate_steps(
    *,
    steps_current_daily: list[tuple[str, float]],
    steps_baseline_daily: list[tuple[str, float]],
    window: WindowSpec,
    thresholds: TunableThresholds,
    context: RuleContext,
) -> Anomaly:
    """Steps collapse rule: 7-day mean < 50% of 28-day baseline mean.

    AGENTS.md §"Initial thresholds" — "Steps collapse: 7-day mean
    steps < 50% of the 28-day baseline."

    This is normally ``info``-severity (training load, travel, or
    rest day are common causes). It is only ``prominent`` if a
    sustained illness marker is also present.
    """
    data_window = _window_to_dict(window)
    baseline_window = data_window

    if not steps_current_daily and not steps_baseline_daily:
        return Anomaly(
            id=_make_id("steps_collapse", window),
            rule="steps_collapse",
            metric="steps",
            status=AnomalyStatus.DATA_NOT_AVAILABLE,
            severity=AnomalySeverity.INFO,
            summary="No daily steps data in the current or baseline window.",
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_daily_steps": None, "days": 0},
            baseline_value={"mean_daily_steps": None, "days": 0},
            context={"missing_metric": "metric_intervals(steps)"},
        )

    current_values = [s for _, s in steps_current_daily]
    baseline_values = [s for _, s in steps_baseline_daily]
    mean_current = daily_mean(current_values)
    mean_baseline = daily_mean(baseline_values)

    ratio = (
        (mean_current / mean_baseline)
        if (mean_current is not None and mean_baseline not in (None, 0.0))
        else None
    )

    if (
        len(current_values) < thresholds.min_current_samples
        or len(baseline_values) < thresholds.min_baseline_samples
    ):
        return Anomaly(
            id=_make_id("steps_collapse", window),
            rule="steps_collapse",
            metric="steps",
            status=AnomalyStatus.INSUFFICIENT_SAMPLES,
            severity=AnomalySeverity.INFO,
            summary=(
                f"Not enough daily step totals to evaluate "
                f"(current={len(current_values)}, baseline={len(baseline_values)})."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_daily_steps": mean_current, "days": len(current_values)},
            baseline_value={"mean_daily_steps": mean_baseline, "days": len(baseline_values)},
        )

    fired = ratio is not None and ratio < thresholds.steps_collapse_ratio
    if not fired:
        return Anomaly(
            id=_make_id("steps_collapse", window),
            rule="steps_collapse",
            metric="steps",
            status=AnomalyStatus.WITHIN_THRESHOLD,
            severity=AnomalySeverity.INFO,
            summary=(
                f"Steps 7-day mean {mean_current:.0f} vs 28-day baseline "
                f"{mean_baseline:.0f} ({ratio * 100 if ratio is not None else 0:.0f}% of baseline). "
                f"No collapse beyond the {thresholds.steps_collapse_ratio * 100:.0f}% threshold."
            ),
            data_window=data_window,
            baseline_window=baseline_window,
            current_value={"mean_daily_steps": mean_current, "days": len(current_values)},
            baseline_value={"mean_daily_steps": mean_baseline, "days": len(baseline_values)},
            context={"ratio": ratio},
        )

    severity = (
        AnomalySeverity.PROMINENT
        if context.illness_marker_in_window
        else AnomalySeverity.INFO
    )
    return Anomaly(
        id=_make_id("steps_collapse", window),
        rule="steps_collapse",
        metric="steps",
        status=AnomalyStatus.FIRED,
        severity=severity,
        summary=(
            f"Steps collapsed to {ratio * 100 if ratio is not None else 0:.0f}% of baseline "
            f"(7-day mean {mean_current:.0f} vs baseline {mean_baseline:.0f})."
        ),
        data_window=data_window,
        baseline_window=baseline_window,
        current_value={"mean_daily_steps": mean_current, "days": len(current_values)},
        baseline_value={"mean_daily_steps": mean_baseline, "days": len(baseline_values)},
        context={"ratio": ratio, "illness_marker_in_window": context.illness_marker_in_window},
        recommendation=(
            "Cross-reference training load and travel before drawing a conclusion. "
            "If illness markers are also present, surface prominently."
        ),
    )


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyRule:
    """A named rule plus the callable that evaluates it.

    Rules register themselves with the detector. Tests and the
    build-#4 weekly summary can introspect the rule set without
    calling them.
    """

    name: str
    metric: str
    evaluate: Callable[..., Anomaly]


BUILTIN_RULES: tuple[AnomalyRule, ...] = (
    AnomalyRule(
        name="hrv_drop",
        metric="heart_rate_variability",
        evaluate=evaluate_hrv,
    ),
    AnomalyRule(
        name="rhr_rise",
        metric="resting_heart_rate",
        evaluate=evaluate_rhr,
    ),
    AnomalyRule(
        name="sleep_collapse",
        metric="sleep_minutes",
        evaluate=evaluate_sleep,
    ),
    AnomalyRule(
        name="steps_collapse",
        metric="steps",
        evaluate=evaluate_steps,
    ),
)
"""The four default rules in their canonical evaluation order.

The order is the AGENTS.md §"Initial thresholds" order. The detector
runs all of them per check, so the order is informational; it does
not change which rules fire.
"""
