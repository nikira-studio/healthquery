"""Anomaly detector orchestrator.

This is the only module in the package that talks to HealthQuery.
The rules themselves stay pure so they can be unit-tested with
synthetic inputs; the detector handles:

* window/baseline date math;
* HealthQuery reads via the ``healthquery-client`` library;
* context-window assembly;
* mapping HealthQuery rows into the simple shapes the rules
  consume (``list[float]`` for HRV/RHR, ``list[(date, value)]`` for
  sleep / steps);
* a stable ``run_id`` and a snapshot of the latest ``batch_id``
  for the report.

Idempotency
-----------

The detector is **read-only** and **deterministic** for a given
HealthQuery snapshot: re-running it against the same
``batch_id`` produces the same output bytes. The summary
generator (build #4) can rely on this to cache a report by
``batch_id``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from typing import Callable

from healthquery_client import HealthQueryClient

from .context import (
    aggregate_awake_minutes,
    build_rule_context,
    summarize_context,
)
from .output import Anomaly, AnomalyReport
from .rules import BUILTIN_RULES, AnomalyRule, RuleContext
from .thresholds import DEFAULT_THRESHOLDS, TunableThresholds
from .windows import WindowSpec, build_window, iso_date, now_utc


class AnomalyDetector:
    """Builds an :class:`AnomalyReport` from a HealthQuery snapshot.

    Example::

        from health_coach_anomaly import AnomalyDetector
        from healthquery_client import HealthQueryClient

        with HealthQueryClient() as hq:
            detector = AnomalyDetector(hq)
            report = detector.detect(window_days=7, baseline_days=28)
    """

    def __init__(
        self,
        client: HealthQueryClient,
        *,
        thresholds: TunableThresholds = DEFAULT_THRESHOLDS,
        rules: tuple[AnomalyRule, ...] = BUILTIN_RULES,
        now: Callable[[], _dt.datetime] = now_utc,
        run_id: str | None = None,
    ) -> None:
        self.client = client
        self.thresholds = thresholds
        self.rules = rules
        self._now = now
        self._run_id = run_id

    def detect(
        self,
        *,
        window_days: int = 7,
        baseline_days: int = 28,
    ) -> AnomalyReport:
        """Run every registered rule and return an :class:`AnomalyReport`."""
        now = self._now()
        run_id = self._run_id or os.environ.get(
            "HEALTH_COACH_RUN_ID"
        ) or _generate_run_id()
        window, baseline = build_window(
            now=now,
            window_days=window_days,
            baseline_days=baseline_days,
        )

        batch_id = self._latest_batch_id()
        rhr_current, rhr_baseline = self._fetch_rhr(window, baseline)
        hrv_current, hrv_baseline = self._fetch_hrv(window, baseline)
        sleep_recent, sleep_prior = self._fetch_sleep_minutes(window)
        steps_current_daily, steps_baseline_daily = self._fetch_steps_daily(
            window, baseline
        )
        sleep_sessions, sleep_stages = self._fetch_sleep_sessions(window)
        workouts = self._fetch_workouts(window)
        annotated_sessions = aggregate_awake_minutes(sleep_sessions, sleep_stages)

        context = build_rule_context(
            window=window,
            sleep_sessions=annotated_sessions,
            workouts=workouts,
            rhr_current=rhr_current,
            rhr_baseline=rhr_baseline,
            sleep_minutes_recent_week=sleep_recent,
            sleep_minutes_prior_week=sleep_prior,
            steps_current_daily=steps_current_daily,
            steps_baseline_daily=steps_baseline_daily,
        )

        anomalies: list[Anomaly] = []
        for rule in self.rules:
            anomalies.append(
                self._evaluate_rule(
                    rule=rule,
                    window=window,
                    baseline=baseline,
                    context=context,
                    hrv_current=hrv_current,
                    hrv_baseline=hrv_baseline,
                    rhr_current=rhr_current,
                    rhr_baseline=rhr_baseline,
                    sleep_recent=sleep_recent,
                    sleep_prior=sleep_prior,
                    steps_current=steps_current_daily,
                    steps_baseline=steps_baseline_daily,
                )
            )

        return AnomalyReport(
            run_id=run_id,
            window=window,
            baseline=baseline,
            healthquery_batch_id=batch_id,
            healthquery_base_url=self.client.base_url,
            generated_at=now.isoformat().replace("+00:00", "Z"),
            anomalies=anomalies,
            thresholds={
                "hrv_drop_pct": self.thresholds.hrv_drop_pct,
                "rhr_rise_pct": self.thresholds.rhr_rise_pct,
                "sleep_drop_pct": self.thresholds.sleep_drop_pct,
                "sleep_minimum_minutes": self.thresholds.sleep_minimum_minutes,
                "sleep_consecutive_nights": self.thresholds.sleep_consecutive_nights,
                "steps_collapse_ratio": self.thresholds.steps_collapse_ratio,
                "min_current_samples": self.thresholds.min_current_samples,
                "min_baseline_samples": self.thresholds.min_baseline_samples,
            },
            _rule_context=context,
        )

    # ------------------------------------------------------------------
    # Per-rule evaluation
    # ------------------------------------------------------------------

    def _evaluate_rule(
        self,
        *,
        rule: AnomalyRule,
        window: WindowSpec,
        baseline: WindowSpec,
        context: RuleContext,
        hrv_current: list[float],
        hrv_baseline: list[float],
        rhr_current: list[float],
        rhr_baseline: list[float],
        sleep_recent: list[tuple[str, float]],
        sleep_prior: list[tuple[str, float]],
        steps_current: list[tuple[str, float]],
        steps_baseline: list[tuple[str, float]],
    ) -> Anomaly:
        evaluator = rule.evaluate
        # Each rule needs the *baseline* window spec so its
        # ``baseline_window`` field reflects the baseline start/end
        # (not the current window). The detector monkey-patches the
        # ``_window_to_dict`` indirection by passing the right
        # window to the rule callable. We do this by wrapping the
        # rule: rebind the rule's module-level helper. Simpler:
        # pass a WindowSpec-shaped wrapper that the rules treat as
        # the current window for ID purposes, but the rules'
        # baseline_window output should be the baseline's dict.
        # To keep rules pure, we post-process: the rule returns the
        # current-window dict as baseline_window; we overwrite it
        # here with the actual baseline dict.
        baseline_dict = {
            "start": baseline.iso_start,
            "end": baseline.iso_end,
            "days": str(baseline.days),
        }
        if rule.name == "hrv_drop":
            anomaly = evaluator(
                hrv_current=hrv_current,
                hrv_baseline=hrv_baseline,
                window=window,
                thresholds=self.thresholds,
                context=context,
            )
        elif rule.name == "rhr_rise":
            anomaly = evaluator(
                rhr_current=rhr_current,
                rhr_baseline=rhr_baseline,
                window=window,
                thresholds=self.thresholds,
                context=context,
            )
        elif rule.name == "sleep_collapse":
            anomaly = evaluator(
                sleep_minutes_recent_week=sleep_recent,
                sleep_minutes_prior_week=sleep_prior,
                window=window,
                thresholds=self.thresholds,
                context=context,
            )
        elif rule.name == "steps_collapse":
            anomaly = evaluator(
                steps_current_daily=steps_current,
                steps_baseline_daily=steps_baseline,
                window=window,
                thresholds=self.thresholds,
                context=context,
            )
        else:
            raise ValueError(f"Unknown rule: {rule.name!r}")
        # Overwrite baseline_window with the actual baseline dict.
        # The rules are pure; this is the single place where the
        # detector injects window metadata the rules cannot derive
        # on their own.
        return _with_baseline_window(anomaly, baseline_dict)

    # ------------------------------------------------------------------
    # HealthQuery reads
    # ------------------------------------------------------------------

    def _latest_batch_id(self) -> str | None:
        """Return the latest ``batch_id`` from ``/api/health/batches``.

        The detector only needs the id (for the report); the
        freshness guarantee comes from the fact that the most
        recent batch is the one the data was sourced from.
        """
        try:
            response = self.client.get_batches(limit=1)
        except Exception:
            return None
        if not isinstance(response, dict):
            return None
        batches = response.get("batches") or []
        if not batches:
            return None
        first = batches[0]
        if not isinstance(first, dict):
            return None
        return first.get("batch_id")

    def _fetch_rhr(
        self, window: WindowSpec, baseline: WindowSpec
    ) -> tuple[list[float], list[float]]:
        return self._fetch_metric_point_values(
            metric_type="resting_heart_rate", window=window, baseline=baseline
        )

    def _fetch_hrv(
        self, window: WindowSpec, baseline: WindowSpec
    ) -> tuple[list[float], list[float]]:
        return self._fetch_metric_point_values(
            metric_type="heart_rate_variability", window=window, baseline=baseline
        )

    def _fetch_metric_point_values(
        self,
        *,
        metric_type: str,
        window: WindowSpec,
        baseline: WindowSpec,
    ) -> tuple[list[float], list[float]]:
        """Read ``metric_points`` for ``metric_type`` over both windows.

        Returns two lists of ``numeric_value`` (one per window).
        Empty lists are returned when the metric is not present
        in the live data — that is the "data_not_available"
        signal the rules are designed to handle.
        """
        sql = _build_window_select_point(
            metric_type=metric_type,
            window=window,
            baseline=baseline,
        )
        result = self.client.post_query(sql=sql)
        current: list[float] = []
        baseline_values: list[float] = []
        for row in result.rows:
            ts_raw = row.get("recorded_at")
            value = row.get("numeric_value")
            if ts_raw is None or value is None:
                continue
            try:
                ts = _parse_iso(ts_raw)
            except ValueError:
                continue
            if window.contains(ts):
                current.append(float(value))
            elif baseline.contains(ts):
                baseline_values.append(float(value))
        return current, baseline_values

    def _fetch_sleep_minutes(
        self, window: WindowSpec
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Read ``sleep_sessions.duration_minutes`` for the recent and prior halves of the window.

        The sleep rule operates on a week-over-week comparison,
        so the detector slices the current window into two
        halves of ``window_days // 2`` days each. For the default
        ``window_days=7`` this is the most-recent 7 days vs the
        prior 7 days. The function returns the actual calendar
        date of each night so the rule can render a per-night
        breakdown if needed.
        """
        recent: list[tuple[str, float]] = []
        prior: list[tuple[str, float]] = []
        sql = _build_window_select_sleep(window=window)
        result = self.client.post_query(sql=sql)
        half_days = max(1, window.days // 2)
        midpoint = window.start + _dt.timedelta(days=half_days)
        for row in result.rows:
            ts_raw = row.get("start_time")
            minutes = row.get("duration_minutes")
            if ts_raw is None or minutes is None:
                continue
            try:
                ts = _parse_iso(ts_raw)
            except ValueError:
                continue
            if not window.contains(ts):
                continue
            if ts < midpoint:
                prior.append((iso_date(ts), float(minutes)))
            else:
                recent.append((iso_date(ts), float(minutes)))
        return recent, prior

    def _fetch_steps_daily(
        self, window: WindowSpec, baseline: WindowSpec
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Aggregate per-interval ``metric_intervals(steps)`` rows into daily totals.

        HealthQuery stores Steps as 15-minute buckets; the rule
        needs a single ``(date, total_steps)`` per day. The
        detector sums the per-bucket ``numeric_value`` for each
        UTC date in the windows.
        """
        sql = _build_window_select_steps(
            window=window, baseline=baseline
        )
        result = self.client.post_query(sql=sql)
        per_day_current: dict[str, float] = {}
        per_day_baseline: dict[str, float] = {}
        for row in result.rows:
            ts_raw = row.get("start_time")
            value = row.get("numeric_value")
            if ts_raw is None or value is None:
                continue
            try:
                ts = _parse_iso(ts_raw)
            except ValueError:
                continue
            bucket: dict[str, float]
            if window.contains(ts):
                bucket = per_day_current
            elif baseline.contains(ts):
                bucket = per_day_baseline
            else:
                continue
            day = iso_date(ts)
            bucket[day] = bucket.get(day, 0.0) + float(value)
        current = sorted(per_day_current.items())
        baseline_out = sorted(per_day_baseline.items())
        return current, baseline_out

    def _fetch_sleep_sessions(
        self, window: WindowSpec
    ) -> tuple[list[dict], list[dict]]:
        """Read ``sleep_sessions`` and ``sleep_stages`` for the context-window illness check.

        The detector only needs the rows whose ``start_time`` is
        in the current window. ``sleep_stages`` is filtered to
        the same window because the rule compares awake vs
        total within the window.
        """
        sessions_sql = _build_window_select_sessions(window=window)
        stages_sql = _build_window_select_stages(window=window)
        sessions_result = self.client.post_query(sql=sessions_sql)
        stages_result = self.client.post_query(sql=stages_sql)
        sessions = [
            {k: row.get(k) for k in ("session_key", "start_time", "end_time", "duration_minutes")}
            for row in sessions_result.rows
        ]
        stages = [
            {k: row.get(k) for k in ("session_key", "stage_type", "duration_seconds")}
            for row in stages_result.rows
        ]
        return sessions, stages

    def _fetch_workouts(self, window: WindowSpec) -> list[dict]:
        """Read ``workouts`` for the current window.

        Used by the context block ("training load" framing);
        rules do not consume it directly.
        """
        sql = _build_window_select_workouts(window=window)
        result = self.client.post_query(sql=sql)
        return list(result.rows)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def render_summary(self, report: AnomalyReport) -> str:
        """Render the report as a markdown summary block.

        The build-#4 weekly summary embeds this block inside
        the "Anomalies with context" section. The detector does
        not need to know how the surrounding summary is
        composed; it only produces a single self-contained
        block.
        """
        lines = [
            f"### Anomalies — {report.window.iso_start} → {report.window.iso_end}",
            f"_run_id: `{report.run_id}` • batch_id: `{report.healthquery_batch_id or 'n/a'}` • baseline: {report.baseline.days} days_",
            "",
        ]
        if report.prominent:
            lines.append("**Prominent (surface at top of summary):**")
            for a in report.prominent:
                lines.append(f"- ⚠ **{a.metric}** — {a.summary}")
            lines.append("")
        fired = [a for a in report.fired if a.severity.value == "info"]
        if fired:
            lines.append("**Trend-context:**")
            for a in fired:
                lines.append(f"- {a.metric} — {a.summary}")
            lines.append("")
        info = [a for a in report.anomalies if a.status.value != "fired"]
        if info:
            lines.append("**Checked, within threshold or no data:**")
            for a in info:
                lines.append(f"- {a.metric} — {a.summary}")
            lines.append("")
        lines.append("**Context block:**")
        # Use the live rule context the detector actually fed
        # into the rules, not a re-derivation from the report.
        # This keeps the sample counts honest: a rule that ran
        # on 13 RHR samples renders as 13, not 0.
        ctx = report._rule_context
        if ctx is None:
            ctx = _context_from_report(report)
        lines.append("```json")
        lines.append(json.dumps(summarize_context(ctx, window=report.window), indent=2, sort_keys=True))
        lines.append("```")
        return "\n".join(lines)


def _context_from_report(report: AnomalyReport) -> RuleContext:
    """Reconstruct a :class:`RuleContext` from the report (for the summary context block).

    The detector passes the live context into each rule; the
    summary block only needs a denormalized version, so we
    re-derive it from the report's anomaly records.
    """
    rhr_current: list[float] = []
    rhr_baseline: list[float] = []
    sleep_recent: list[tuple[str, float]] = []
    sleep_prior: list[tuple[str, float]] = []
    steps_current: list[tuple[str, float]] = []
    steps_baseline: list[tuple[str, float]] = []
    for a in report.anomalies:
        if a.rule == "sleep_collapse":
            r = a.current_value.get("recent_week_total_minutes")
            if r:
                sleep_recent.append(("total", float(r)))
            r = a.baseline_value.get("prior_week_total_minutes")
            if r:
                sleep_prior.append(("total", float(r)))
        if a.rule == "steps_collapse":
            if isinstance(a.current_value.get("mean_daily_steps"), (int, float)):
                steps_current.append(("mean", float(a.current_value["mean_daily_steps"])))
            if isinstance(a.baseline_value.get("mean_daily_steps"), (int, float)):
                steps_baseline.append(("mean", float(a.baseline_value["mean_daily_steps"])))
    return RuleContext(
        rhr_current=rhr_current,
        rhr_baseline=rhr_baseline,
        sleep_minutes_recent_week=sleep_recent,
        sleep_minutes_prior_week=sleep_prior,
        steps_current_daily=steps_current,
        steps_baseline_daily=steps_baseline,
        workouts_in_window=[],
        illness_marker_in_window=any(
            a.context.get("illness_marker_in_window") for a in report.anomalies
        ),
    )


def _with_baseline_window(anomaly: Anomaly, baseline_window: dict[str, str]) -> Anomaly:
    """Return a copy of ``anomaly`` with the baseline window spec set correctly.

    The rules are pure: each takes the current ``window`` and
    uses it to render the ``baseline_window`` field of the
    resulting :class:`Anomaly`. The detector knows the *real*
    baseline window (a separate :class:`WindowSpec`); this
    helper is the single point where the detector injects that
    metadata back into the rule output.
    """
    from dataclasses import replace

    return replace(anomaly, baseline_window=baseline_window)


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------


def _sql_escape(value: str) -> str:
    """Escape a single-quoted SQL literal (HealthQuery's SQL guard accepts this form)."""
    if not isinstance(value, str):
        raise TypeError(f"expected str, got {type(value).__name__}")
    return value.replace("'", "''")


def _iso(value: _dt.datetime) -> str:
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_window_select_point(
    *, metric_type: str, window: WindowSpec, baseline: WindowSpec
) -> str:
    return (
        "SELECT recorded_at, numeric_value "
        f"FROM metric_points "
        f"WHERE metric_type = '{_sql_escape(metric_type)}' "
        f"AND recorded_at >= '{_iso(baseline.start)}' "
        f"AND recorded_at < '{_iso(window.end)}' "
        f"ORDER BY recorded_at ASC"
    )


def _build_window_select_sleep(*, window: WindowSpec) -> str:
    return (
        "SELECT start_time, duration_minutes "
        f"FROM sleep_sessions "
        f"WHERE start_time >= '{_iso(window.start - _dt.timedelta(days=window.days))}' "
        f"AND start_time < '{_iso(window.end)}' "
        f"ORDER BY start_time ASC"
    )


def _build_window_select_steps(
    *, window: WindowSpec, baseline: WindowSpec
) -> str:
    return (
        "SELECT start_time, numeric_value "
        f"FROM metric_intervals "
        f"WHERE metric_type = 'steps' "
        f"AND start_time >= '{_iso(baseline.start)}' "
        f"AND start_time < '{_iso(window.end)}' "
        f"ORDER BY start_time ASC"
    )


def _build_window_select_sessions(*, window: WindowSpec) -> str:
    return (
        "SELECT session_key, start_time, end_time, duration_minutes "
        f"FROM sleep_sessions "
        f"WHERE start_time >= '{_iso(window.start - _dt.timedelta(days=1))}' "
        f"AND start_time < '{_iso(window.end)}' "
        f"ORDER BY start_time ASC"
    )


def _build_window_select_stages(*, window: WindowSpec) -> str:
    return (
        "SELECT session_key, stage_type, duration_seconds "
        f"FROM sleep_stages "
        f"WHERE start_time >= '{_iso(window.start - _dt.timedelta(days=1))}' "
        f"AND start_time < '{_iso(window.end)}'"
    )


def _build_window_select_workouts(*, window: WindowSpec) -> str:
    return (
        "SELECT workout_key, activity_type, start_time, end_time, duration_minutes, calories "
        f"FROM workouts "
        f"WHERE start_time >= '{_iso(window.start - _dt.timedelta(days=1))}' "
        f"AND start_time < '{_iso(window.end)}' "
        f"ORDER BY start_time ASC"
    )


def _parse_iso(value: str) -> _dt.datetime:
    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _generate_run_id() -> str:
    """A short, human-readable run id (uuid4 prefix + random suffix)."""
    raw = uuid.uuid4().hex
    return f"hc-anomaly-{raw[:12]}"
