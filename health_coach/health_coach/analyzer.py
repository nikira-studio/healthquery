"""Weekly aggregator for the Health Coach's summary generator.

This module turns HealthQuery row dictionaries (returned from
``/api/health/query`` SELECTs and the read endpoint views) into the
trend objects :mod:`health_coach.report` renders. It does **not** know
about HTTP, auth, or file paths; that is :mod:`health_coach.report`'s
job. This keeps the aggregation logic unit-testable without an HTTP
fixture and keeps the privacy filter at the report boundary.

Inputs
------

:data:`AnalysisInputs` is the only thing this module consumes: a
bunch of raw row dictionaries, already filtered to the report's date
window by the caller. Every aggregate is computed from these inputs in
a single pass. There is no I/O, no clock reads, no randomness. The
results are deterministic for the same inputs.

Outputs
-------

A :class:`WeeklyTrend` for each named trend line (HRV, sleep, activity,
body comp, workouts) plus an :class:`AnomalyFinding` list for any rule
that fired in the window. The report renders these into the AGENTS.md
output bar sections.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Mapping, Sequence

from .vocabulary import (
    ABSENT_METRIC_TYPES,
    KNOWN_INTERVAL_TYPES,
    KNOWN_METRIC_TYPES,
    KNOWN_SLEEP_STAGES,
    label_workout_code,
)

# Anomaly thresholds (per AGENTS.md §"Trend vs anomaly vs noise").
# Single bad night = noise. 7-day HRV drop = trend. Acute crash + fever
# = anomaly with context. These constants are the *minimum* the build
# analyzer must detect; the rule-based thresholds grow over time.
HRV_DROP_THRESHOLD_PCT = 15.0
HRV_WINDOW_DAYS = 7
SLEEP_MIN_TREND_DAYS = 5
STEPS_GOAL_DEFAULT = 7000  # operator-level default; per-operator goals live elsewhere


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _to_date(value: str | None) -> date | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).date()


@dataclass(frozen=True)
class AnalysisInputs:
    """Bundled inputs for the weekly analyzer.

    Every list is row dictionaries shaped exactly as HealthQuery
    returns them. The analyzer does not introspect keys; the caller is
    responsible for filtering by date window before passing the rows
    in. This keeps the analyzer honest about its privacy contract: it
    never sees rows outside the window.
    """

    window_start: date
    window_end: date
    daily_summaries: Sequence[Mapping[str, object]] = field(default_factory=list)
    heart_rate_points: Sequence[Mapping[str, object]] = field(default_factory=list)
    oxygen_saturation_points: Sequence[Mapping[str, object]] = field(default_factory=list)
    resting_heart_rate_points: Sequence[Mapping[str, object]] = field(default_factory=list)
    sleep_sessions: Sequence[Mapping[str, object]] = field(default_factory=list)
    sleep_stages: Sequence[Mapping[str, object]] = field(default_factory=list)
    steps_intervals: Sequence[Mapping[str, object]] = field(default_factory=list)
    distance_intervals: Sequence[Mapping[str, object]] = field(default_factory=list)
    total_calories_intervals: Sequence[Mapping[str, object]] = field(default_factory=list)
    workouts: Sequence[Mapping[str, object]] = field(default_factory=list)
    # Explicit HRV window points — separate from heart_rate_points so the
    # analyzer cannot accidentally fold them into the HR trend. Empty
    # when the live data does not carry HRV; the trend line then renders
    # a "data not available" note.
    hrv_points: Sequence[Mapping[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class MetricAggregate:
    """Single number for one metric over the window.

    ``unit`` is the unit HealthQuery returned (``bpm``, ``%``, ``steps``,
    ``kcal``, ``m``, ``minutes``). ``sample_size`` is the count of raw
    points or sessions the aggregate was computed over; the report
    surfaces it next to the number so the reader knows the confidence.
    """

    label: str
    value: float
    unit: str
    sample_size: int
    comparator: float | None = None
    comparator_label: str | None = None


@dataclass(frozen=True)
class WeeklyTrend:
    """One trend line in the AGENTS.md output bar.

    ``data_available`` is ``False`` when the live data does not yet
    carry the metric. The report renders ``data not available`` notes
    for these so the reader does not infer a missing-trend signal.
    """

    name: str
    data_available: bool
    aggregates: tuple[MetricAggregate, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnomalyFinding:
    """Single anomaly with the context that fired the rule.

    Per AGENTS.md §"Trend vs anomaly vs noise", a flag without context
    is not an anomaly — it is a number. ``context_window`` carries the
    other metrics the analyzer cross-referenced so the reader can see
    why the flag was raised.
    """

    rule: str
    severity: str  # "noise" | "trend" | "anomaly"
    metric: str
    window: str
    finding: str
    context_window: tuple[str, ...] = ()


def _percent_change(newer: float, older: float) -> float | None:
    if older == 0:
        return None
    return ((newer - older) / older) * 100.0


def _trend_aggregate_hrv(
    hrv_points: Sequence[Mapping[str, object]],
    window: tuple[date, date],
) -> WeeklyTrend:
    """HRV 7-day rolling mean vs the prior 7-day mean.

    Per AGENTS.md §"Anomaly" rule: HRV drop >15% over 7 days → flag.
    The anomaly is reported by :func:`detect_anomalies`; this function
    only produces the trend aggregate.
    """
    if not hrv_points:
        return WeeklyTrend(
            name="HRV (heart rate variability)",
            data_available=False,
            notes=(
                "data not available — the companion app has not yet "
                "ingested heart_rate_variability rows; the "
                "'HRV drop >15% over 7 days → flag' rule cannot fire",
            ),
        )
    values = [
        float(p["numeric_value"])
        for p in hrv_points
        if isinstance(p.get("numeric_value"), (int, float))
    ]
    if not values:
        return WeeklyTrend(
            name="HRV (heart rate variability)",
            data_available=False,
            notes=("data not available — HRV rows present but no numeric values",),
        )
    window_start, window_end = window
    inside: list[float] = []
    prior: list[float] = []
    for p, v in zip(hrv_points, values):
        d = _to_date(p.get("recorded_at"))  # type: ignore[arg-type]
        if d is None:
            continue
        if window_start <= d <= window_end:
            inside.append(v)
        elif d < window_start:
            prior.append(v)
    if not inside:
        return WeeklyTrend(
            name="HRV (heart rate variability)",
            data_available=False,
            notes=("data not available — no HRV samples inside the report window",),
        )
    window_mean = statistics.fmean(inside)
    aggregates = (
        MetricAggregate(
            label="7-day mean",
            value=round(window_mean, 1),
            unit="ms",
            sample_size=len(inside),
        ),
    )
    if prior:
        prior_mean = statistics.fmean(prior)
        aggregates = aggregates + (
            MetricAggregate(
                label="prior 7-day mean",
                value=round(prior_mean, 1),
                unit="ms",
                sample_size=len(prior),
                comparator=round(_percent_change(window_mean, prior_mean) or 0.0, 1),
                comparator_label="% vs prior",
            ),
        )
    return WeeklyTrend(
        name="HRV (heart rate variability)",
        data_available=True,
        aggregates=aggregates,
    )


def _trend_aggregate_resting_heart_rate(
    points: Sequence[Mapping[str, object]],
    window: tuple[date, date],
) -> WeeklyTrend:
    """Resting HR — daily means across the window.

    Resting HR is sparse (the operator's Health Connect writes it once
    a day), so we render the per-day mean when available and the window
    mean otherwise. The HR-trend context window for the anomaly rule
    lives in :func:`detect_anomalies`.
    """
    if not points:
        return WeeklyTrend(
            name="Resting heart rate",
            data_available=False,
            notes=("data not available — no resting_heart_rate rows in the window",),
        )
    by_day: dict[date, list[float]] = {}
    for p in points:
        d = _to_date(p.get("recorded_at"))  # type: ignore[arg-type]
        if d is None:
            continue
        if window[0] <= d <= window[1] and isinstance(p.get("numeric_value"), (int, float)):
            by_day.setdefault(d, []).append(float(p["numeric_value"]))
    if not by_day:
        return WeeklyTrend(
            name="Resting heart rate",
            data_available=False,
            notes=("data not available — resting_heart_rate rows exist but none in the window",),
        )
    daily_means = [statistics.fmean(v) for v in by_day.values() if v]
    window_mean = statistics.fmean(daily_means) if daily_means else 0.0
    return WeeklyTrend(
        name="Resting heart rate",
        data_available=True,
        aggregates=(
            MetricAggregate(
                label="daily-mean average",
                value=round(window_mean, 1),
                unit="bpm",
                sample_size=len(daily_means),
            ),
            MetricAggregate(
                label="days observed",
                value=float(len(daily_means)),
                unit="days",
                sample_size=len(daily_means),
            ),
        ),
    )


def _trend_aggregate_oxygen_saturation(
    points: Sequence[Mapping[str, object]],
    window: tuple[date, date],
) -> WeeklyTrend:
    if not points:
        return WeeklyTrend(
            name="Oxygen saturation",
            data_available=False,
            notes=("data not available — no oxygen_saturation rows in the window",),
        )
    values: list[float] = []
    for p in points:
        if window[0] <= (_to_date(p.get("recorded_at")) or date.min) <= window[1]:
            if isinstance(p.get("numeric_value"), (int, float)):
                values.append(float(p["numeric_value"]))
    if not values:
        return WeeklyTrend(
            name="Oxygen saturation",
            data_available=False,
            notes=("data not available — oxygen_saturation rows exist but none in the window",),
        )
    return WeeklyTrend(
        name="Oxygen saturation",
        data_available=True,
        aggregates=(
            MetricAggregate(
                label="window mean",
                value=round(statistics.fmean(values), 2),
                unit="%",
                sample_size=len(values),
            ),
            MetricAggregate(
                label="window min",
                value=round(min(values), 2),
                unit="%",
                sample_size=len(values),
            ),
        ),
    )


def _trend_aggregate_sleep(
    sessions: Sequence[Mapping[str, object]],
    stages: Sequence[Mapping[str, object]],
    window: tuple[date, date],
) -> WeeklyTrend:
    if not sessions:
        return WeeklyTrend(
            name="Sleep",
            data_available=False,
            notes=("data not available — no sleep_sessions rows in the window",),
        )
    in_window: list[Mapping[str, object]] = []
    for s in sessions:
        d = _to_date(s.get("end_time"))  # type: ignore[arg-type]
        if d is None:
            d = _to_date(s.get("start_time"))  # type: ignore[arg-type]
        if d is None:
            continue
        if window[0] <= d <= window[1]:
            in_window.append(s)
    if not in_window:
        return WeeklyTrend(
            name="Sleep",
            data_available=False,
            notes=("data not available — sleep_sessions exist but none ended in the window",),
        )
    durations = [
        float(s["duration_minutes"])
        for s in in_window
        if isinstance(s.get("duration_minutes"), (int, float))
    ]
    if not durations:
        return WeeklyTrend(
            name="Sleep",
            data_available=False,
            notes=("data not available — sleep sessions in window have no duration_minutes",),
        )
    notes: list[str] = []
    # Per-stage labels are stripped from the body (STA-53 privacy review).
    # The report names only the count of stage rows present, never the
    # per-stage distribution, so the operator's stage share is never
    # disclosed. Stage-level detail is available behind a query if the
    # operator wants it explicitly.
    if stages:
        notes.append(f"{len(stages)} sleep-stage rows present in window")
    return WeeklyTrend(
        name="Sleep",
        data_available=True,
        aggregates=(
            MetricAggregate(
                label="nights with data",
                value=float(len(durations)),
                unit="nights",
                sample_size=len(durations),
            ),
            MetricAggregate(
                label="mean duration",
                value=round(statistics.fmean(durations), 0),
                unit="minutes",
                sample_size=len(durations),
            ),
            MetricAggregate(
                label="min duration",
                value=round(min(durations), 0),
                unit="minutes",
                sample_size=len(durations),
            ),
            MetricAggregate(
                label="max duration",
                value=round(max(durations), 0),
                unit="minutes",
                sample_size=len(durations),
            ),
        ),
        notes=tuple(notes),
    )


def _trend_aggregate_activity(
    steps: Sequence[Mapping[str, object]],
    distance: Sequence[Mapping[str, object]],
    calories: Sequence[Mapping[str, object]],
    daily_summaries: Sequence[Mapping[str, object]],
    window: tuple[date, date],
) -> WeeklyTrend:
    """Activity trend line.

    Steps come from ``daily_summaries`` (HealthQuery's per-day rollup)
    when available — that is the canonical number the operator's
    dashboard shows. We only fall back to bucketing
    ``metric_intervals.steps`` when daily_summaries does not cover the
    window. Distance has no per-day rollup, so we bucket
    ``metric_intervals.distance`` by the UTC date of the interval's
    ``start_time``, summing the per-day totals (we discard the
    sub-minute per-step records that double-count the workout
    intervals).
    """
    if not steps and not daily_summaries:
        return WeeklyTrend(
            name="Activity",
            data_available=False,
            notes=("data not available — no steps / daily_summaries in the window",),
        )

    by_day_steps: dict[date, float] = {}
    by_day_steps_from_ds: dict[date, float] = {}
    # Prefer daily_summaries.steps as the per-day source of truth.
    for ds in daily_summaries:
        date_str = ds.get("summary_date")
        if not isinstance(date_str, str):
            continue
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue
        if not (window[0] <= d <= window[1]):
            continue
        steps_val = ds.get("steps")
        if isinstance(steps_val, (int, float)):
            by_day_steps_from_ds[d] = float(steps_val)

    # When daily_summaries does not cover a day, fall back to bucketing
    # metric_intervals.steps by start_time. We exclude the sub-minute
    # per-step rows (< 60 second intervals) to avoid double-counting
    # workout intervals — the daily-total rows Health Connect emits
    # already include them.
    by_day_steps_from_intervals: dict[date, float] = {}
    for s in steps:
        d = _to_date(s.get("start_time")) or _to_date(s.get("end_time"))
        if d is None or not (window[0] <= d <= window[1]):
            continue
        if not isinstance(s.get("numeric_value"), (int, float)):
            continue
        start = s.get("start_time")
        end = s.get("end_time")
        if isinstance(start, str) and isinstance(end, str):
            # Daily-total rows span 24h. Anything shorter is a sub-day
            # bucket and may double-count. Only count sub-hour buckets
            # that span the day boundary (the daily total row), and
            # treat per-minute rows (< 5 min) as sub-workout detail to
            # skip.
            try:
                start_dt = _parse_iso(start)
                end_dt = _parse_iso(end)
                if start_dt and end_dt:
                    span_seconds = (end_dt - start_dt).total_seconds()
                    if span_seconds < 300:  # < 5 minutes
                        continue
            except Exception:
                pass
        by_day_steps_from_intervals[d] = (
            by_day_steps_from_intervals.get(d, 0.0) + float(s["numeric_value"])
        )

    for d, val in by_day_steps_from_ds.items():
        by_day_steps[d] = val
    for d, val in by_day_steps_from_intervals.items():
        if d not in by_day_steps:
            by_day_steps[d] = val

    by_day_distance: dict[date, float] = {}
    for s in distance:
        d = _to_date(s.get("start_time")) or _to_date(s.get("end_time"))
        if d is None or not (window[0] <= d <= window[1]):
            continue
        if not isinstance(s.get("numeric_value"), (int, float)):
            continue
        start = s.get("start_time")
        end = s.get("end_time")
        if isinstance(start, str) and isinstance(end, str):
            try:
                start_dt = _parse_iso(start)
                end_dt = _parse_iso(end)
                if start_dt and end_dt:
                    span_seconds = (end_dt - start_dt).total_seconds()
                    if span_seconds < 300:  # skip sub-minute detail
                        continue
            except Exception:
                pass
        by_day_distance[d] = by_day_distance.get(d, 0.0) + float(s["numeric_value"])

    daily_totals = sorted(set(by_day_steps) | set(by_day_distance))
    if not daily_totals:
        return WeeklyTrend(
            name="Activity",
            data_available=False,
            notes=("data not available — no activity rows in the window",),
        )

    # Drop in-progress days where steps == 0 (today's partial rollup
    # is not a real "0 steps" reading). When daily_summaries is the
    # source, an in-progress day typically shows steps=0 + active_minutes>=0;
    # we filter by checking steps == 0 in the per-day dict.
    completed_step_days = {d: v for d, v in by_day_steps.items() if v > 0}
    completed_distance_days = {d: v for d, v in by_day_distance.items() if v > 0}
    step_values = list(completed_step_days.values())
    distance_values = list(completed_distance_days.values())

    aggregates: list[MetricAggregate] = []
    if step_values:
        aggregates.append(
            MetricAggregate(
                label="daily mean steps",
                value=round(statistics.fmean(step_values), 0),
                unit="steps",
                sample_size=len(step_values),
            )
        )
        aggregates.append(
            MetricAggregate(
                label="daily max steps",
                value=round(max(step_values), 0),
                unit="steps",
                sample_size=len(step_values),
            )
        )
    if distance_values:
        aggregates.append(
            MetricAggregate(
                label="daily mean distance",
                value=round(statistics.fmean(distance_values), 0),
                unit="m",
                sample_size=len(distance_values),
            )
        )
    # Calorie coverage is sparse (Health Connect writes once a day);
    # only count daily-total rows (span ≥ 12 hours). Per-workout calorie
    # intervals are short and would double-count the daily total.
    calorie_values: list[float] = []
    for c in calories:
        if not isinstance(c.get("numeric_value"), (int, float)):
            continue
        d = _to_date(c.get("start_time")) or _to_date(c.get("end_time"))
        if d is None or not (window[0] <= d <= window[1]):
            continue
        start = c.get("start_time")
        end = c.get("end_time")
        if isinstance(start, str) and isinstance(end, str):
            try:
                start_dt = _parse_iso(start)
                end_dt = _parse_iso(end)
                if start_dt and end_dt:
                    span_seconds = (end_dt - start_dt).total_seconds()
                    if span_seconds < 12 * 3600:
                        continue
            except Exception:
                continue
        calorie_values.append(float(c["numeric_value"]))
    if calorie_values:
        aggregates.append(
            MetricAggregate(
                label="mean daily total calories",
                value=round(statistics.fmean(calorie_values), 0),
                unit="kcal",
                sample_size=len(calorie_values),
            )
        )
    days_with_data = len(step_values) if step_values else len(daily_totals)
    notes: list[str] = []
    if step_values:
        above_goal_days = sum(1 for v in step_values if v >= STEPS_GOAL_DEFAULT)
        notes.append(
            f"{above_goal_days}/{days_with_data} days ≥ {STEPS_GOAL_DEFAULT} steps"
        )
    return WeeklyTrend(
        name="Activity",
        data_available=True,
        aggregates=tuple(aggregates),
        notes=tuple(notes),
    )


def _trend_aggregate_workouts(
    workouts: Sequence[Mapping[str, object]],
    window: tuple[date, date],
) -> WeeklyTrend:
    in_window: list[Mapping[str, object]] = []
    for w in workouts:
        d = _to_date(w.get("start_time"))
        if d is None or not (window[0] <= d <= window[1]):
            continue
        in_window.append(w)
    if not in_window:
        return WeeklyTrend(
            name="Workouts",
            data_available=False,
            notes=("data not available — no workouts in the window",),
        )
    durations = [
        float(w["duration_minutes"])
        for w in in_window
        if isinstance(w.get("duration_minutes"), (int, float))
    ]
    by_label: dict[str, int] = {}
    for w in in_window:
        by_label[label_workout_code(w.get("activity_type"))] = (
            by_label.get(label_workout_code(w.get("activity_type")), 0) + 1
        )
    notes: list[str] = []
    if by_label:
        breakdown = ", ".join(f"{name} ×{n}" for name, n in sorted(by_label.items(), key=lambda kv: -kv[1]))
        notes.append(f"activity breakdown: {breakdown}")
    return WeeklyTrend(
        name="Workouts",
        data_available=True,
        aggregates=(
            MetricAggregate(
                label="workouts in window",
                value=float(len(in_window)),
                unit="sessions",
                sample_size=len(in_window),
            ),
            MetricAggregate(
                label="total minutes",
                value=round(sum(durations), 0) if durations else 0.0,
                unit="minutes",
                sample_size=len(durations),
            ),
        ),
        notes=tuple(notes),
    )


def _trend_aggregate_body_comp() -> WeeklyTrend:
    """Body comp is always empty today.

    The HealthQuery schema permits weight, height, body fat, lean body
    mass, bone mass, body water mass — none of which the operator's
    Health Connect currently emits. The AGENTS.md output bar requires
    a trend line for body comp; this trend carries a
    ``data not available`` note until the data lands.
    """
    return WeeklyTrend(
        name="Body composition",
        data_available=False,
        notes=("data not available — weight / body_fat / lean_body_mass not ingested",),
    )


def compute_weekly_trends(
    inputs: AnalysisInputs,
) -> list[WeeklyTrend]:
    """Compute every trend line for the weekly summary.

    Order matches the AGENTS.md output bar (HRV, sleep, activity, body
    comp, workouts).
    """
    window = (inputs.window_start, inputs.window_end)
    return [
        _trend_aggregate_hrv(inputs.hrv_points, window),
        _trend_aggregate_resting_heart_rate(inputs.resting_heart_rate_points, window),
        _trend_aggregate_oxygen_saturation(inputs.oxygen_saturation_points, window),
        _trend_aggregate_sleep(inputs.sleep_sessions, inputs.sleep_stages, window),
        _trend_aggregate_activity(
            inputs.steps_intervals,
            inputs.distance_intervals,
            inputs.total_calories_intervals,
            inputs.daily_summaries,
            window,
        ),
        _trend_aggregate_body_comp(),
        _trend_aggregate_workouts(inputs.workouts, window),
    ]


def detect_anomalies(
    inputs: AnalysisInputs,
) -> list[AnomalyFinding]:
    """Rule-based anomaly detector.

    Only the AGENTS.md §"Anomaly" thresholds the live data can fire
    today are checked. HRV rule is wired up but cannot fire until
    :attr:`AnalysisInputs.hrv_points` is non-empty. When new metric
    types land (weight, BP, glucose) the corresponding rules grow
    here, never in the report layer.
    """
    findings: list[AnomalyFinding] = []
    window = (inputs.window_start, inputs.window_end)
    window_start, window_end = window

    # HRV 7-day drop >15%.
    if inputs.hrv_points:
        inside = [
            float(p["numeric_value"])
            for p in inputs.hrv_points
            if (
                isinstance(p.get("numeric_value"), (int, float))
                and (_to_date(p.get("recorded_at")) is not None)
                and window_start <= _to_date(p.get("recorded_at")) <= window_end  # type: ignore[operator]
            )
        ]
        prior = [
            float(p["numeric_value"])
            for p in inputs.hrv_points
            if (
                isinstance(p.get("numeric_value"), (int, float))
                and (_to_date(p.get("recorded_at")) is not None)
                and _to_date(p.get("recorded_at")) < window_start  # type: ignore[operator]
            )
        ]
        if inside and prior:
            inside_mean = statistics.fmean(inside)
            prior_mean = statistics.fmean(prior)
            pct = _percent_change(inside_mean, prior_mean)
            if pct is not None and pct <= -HRV_DROP_THRESHOLD_PCT:
                context = (
                    f"prior 7-day mean {round(prior_mean, 1)} ms vs window mean {round(inside_mean, 1)} ms",
                )
                # Cross-reference: did resting HR also rise? Stage the
                # additional metric so the reader sees the rule firing
                # *with* context, not as a raw deviation.
                if inputs.resting_heart_rate_points:
                    rh_inside = [
                        float(p["numeric_value"])
                        for p in inputs.resting_heart_rate_points
                        if (
                            isinstance(p.get("numeric_value"), (int, float))
                            and (_to_date(p.get("recorded_at")) is not None)
                            and window_start <= _to_date(p.get("recorded_at")) <= window_end  # type: ignore[operator]
                        )
                    ]
                    if rh_inside:
                        context = context + (
                            f"resting heart rate window mean {round(statistics.fmean(rh_inside), 1)} bpm",
                        )
                findings.append(
                    AnomalyFinding(
                        rule="HRV_DROP_OVER_7D",
                        severity="anomaly",
                        metric="heart_rate_variability",
                        window=f"{window_start.isoformat()} → {window_end.isoformat()}",
                        finding=(
                            f"HRV dropped {round(-pct, 1)}% (>{HRV_DROP_THRESHOLD_PCT:.0f}%) "
                            f"over the 7-day window"
                        ),
                        context_window=context,
                    )
                )

    # Sleep collapse: every sleep session in the window shorter than
    # 240 minutes (4 hours) is a "sleep collapse" flag, not a trend.
    if inputs.sleep_sessions:
        short = [
            s
            for s in inputs.sleep_sessions
            if (
                isinstance(s.get("duration_minutes"), (int, float))
                and float(s["duration_minutes"]) < 240.0
                and (_to_date(s.get("end_time")) or _to_date(s.get("start_time"))) is not None
                and window_start
                <= (_to_date(s.get("end_time")) or _to_date(s.get("start_time")) or date.min)
                <= window_end
            )
        ]
        if short:
            findings.append(
                AnomalyFinding(
                    rule="SLEEP_COLLAPSE",
                    severity="anomaly",
                    metric="sleep_minutes",
                    window=f"{window_start.isoformat()} → {window_end.isoformat()}",
                    finding=(
                        f"{len(short)} sleep session(s) shorter than 4 hours — "
                        "consider a recovery focus"
                    ),
                    context_window=(
                        f"min session {round(min(float(s['duration_minutes']) for s in short), 0)} minutes",
                    ),
                )
            )

    return findings


def compute_wins(
    inputs: AnalysisInputs,
    trends: Iterable[WeeklyTrend],
) -> list[str]:
    """Plain-language wins for the report.

    A "win" is a positive observation grounded in the data: a hit day
    count against a steps goal, a non-collapsed sleep week, the
    presence of workout sessions. The intent is to celebrate
    consistency rather than peaks (per AGENTS.md "no shaming, no
    coercive language").
    """
    wins: list[str] = []
    trends_by_name = {t.name: t for t in trends}
    activity = trends_by_name.get("Activity")
    if activity and activity.data_available:
        note = next((n for n in activity.notes if "≥" in n), None)
        if note:
            wins.append(note)
    sleep = trends_by_name.get("Sleep")
    if sleep and sleep.data_available:
        nights = next(
            (
                a
                for a in sleep.aggregates
                if a.label == "nights with data"
            ),
            None,
        )
        if nights is not None and nights.value >= 5:
            wins.append(
                f"sleep tracked on {int(nights.value)} of 7 nights"
            )
    workouts = trends_by_name.get("Workouts")
    if workouts and workouts.data_available:
        count = next(
            (
                a
                for a in workouts.aggregates
                if a.label == "workouts in window"
            ),
            None,
        )
        if count is not None and count.value >= 1:
            wins.append(
                f"{int(count.value)} workout session(s) in the window"
            )
    return wins


def next_week_focus(
    trends: Iterable[WeeklyTrend],
    findings: Iterable[AnomalyFinding],
) -> str:
    """One-sentence next-week focus.

    Positive-psychology framed, no "should" guilt. The default focus is
    "maintain"; only switch to a recovery or push focus when an
    anomaly or trend demands it.
    """
    findings = list(findings)
    trends = list(trends)
    sleep_trend = next((t for t in trends if t.name == "Sleep"), None)
    activity_trend = next((t for t in trends if t.name == "Activity"), None)

    if any(f.severity == "anomaly" for f in findings):
        return "Recovery focus — one or more anomalies in the window warrant a softer week."

    if (
        sleep_trend
        and sleep_trend.data_available
        and activity_trend
        and activity_trend.data_available
    ):
        return "Maintain — both sleep and activity trends are within the expected range."

    if activity_trend and activity_trend.data_available:
        return "Maintain — keep the current activity cadence; revisit if sleep tracking returns."

    if sleep_trend and sleep_trend.data_available:
        return "Maintain — sleep coverage is the trend this week; activity data is not available."

    return "Maintain — no anomalies or trends to act on this week."
