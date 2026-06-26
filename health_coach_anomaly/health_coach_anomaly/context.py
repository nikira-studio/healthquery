"""Context-window co-movement + illness/training-load context.

The detector pulls the context block **once per run** and feeds it
into every rule. This keeps the rules themselves pure (no
HealthQuery calls) and makes the context block reusable: the
build-#4 weekly summary can render the same context inline.

Three context signals:

* **Co-movement.** Other metrics in the same window. The HRV rule
  consults the RHR current/baseline; the RHR rule consults the
  HRV current/baseline. Sleep and steps each compute their own
  co-movement.
* **Illness markers.** "Awake percentage" spike in the most recent
  sleep_sessions (the operator's wearable emits an ``awake``
  ``stage_type`` row that spikes during fever or restless sleep).
  The rule fires as ``prominent`` when this signal is present.
* **Training load.** Workouts in the current window. The detector
  includes the count and the total minutes; the weekly summary
  uses it to frame a steps collapse as "training" or "illness".

The context block is **plain data** — no HealthQuery types, no
client objects. The detector translates HealthQuery rows into
lists / dicts here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .rules import RuleContext
from .windows import WindowSpec, iso_date


AWAKENESS_SPIKE_THRESHOLD = 0.20
"""A sleep session with awake-minutes / total-minutes > 20% is
flagged as a possible illness marker. The number is intentionally
conservative: the operator can tighten it later via the operator
config; v1 uses the default.
"""


def build_rule_context(
    *,
    window: WindowSpec,
    sleep_sessions: list[dict],
    workouts: list[dict],
    rhr_current: list[float],
    rhr_baseline: list[float],
    sleep_minutes_recent_week: list[tuple[str, float]],
    sleep_minutes_prior_week: list[tuple[str, float]],
    steps_current_daily: list[tuple[str, float]],
    steps_baseline_daily: list[tuple[str, float]],
) -> RuleContext:
    """Assemble the per-rule context block.

    The caller (the detector) has already mapped HealthQuery rows
    to the simple ``list[float]`` / ``list[(date, value)]`` shapes
    the rules consume; this function adds the cross-cutting
    signals (illness markers, training load).
    """
    illness = _detect_illness_marker(sleep_sessions)
    _ = workouts  # currently unused; training-load framing lives in the summary layer
    return RuleContext(
        rhr_current=rhr_current,
        rhr_baseline=rhr_baseline,
        sleep_minutes_recent_week=sleep_minutes_recent_week,
        sleep_minutes_prior_week=sleep_minutes_prior_week,
        steps_current_daily=steps_current_daily,
        steps_baseline_daily=steps_baseline_daily,
        workouts_in_window=workouts,
        illness_marker_in_window=illness,
    )


def _detect_illness_marker(sleep_sessions: list[dict]) -> bool:
    """True if any sleep session in the list has an awake percentage above the threshold.

    Each session is expected to be a dict with at least
    ``start_time`` (ISO), ``duration_minutes`` (float, total
    session), and ``awake_minutes`` (float, sum of the
    ``stage_type='awake'`` rows for that session).
    """
    for session in sleep_sessions:
        total = float(session.get("duration_minutes") or 0.0)
        awake = float(session.get("awake_minutes") or 0.0)
        if total <= 0:
            continue
        if (awake / total) > AWAKENESS_SPIKE_THRESHOLD:
            return True
    return False


def aggregate_awake_minutes(
    sleep_sessions: list[dict],
    sleep_stages: Iterable[dict],
) -> list[dict]:
    """Annotate each sleep session with its total ``awake_minutes``.

    HealthQuery stores sleep stages as a separate table; the
    detector joins them in Python rather than running a SQL join
    (the row volumes are small and this keeps the read path
    transparent). Returns a copy of ``sleep_sessions`` with the
    added ``awake_minutes`` field.
    """
    awake_by_session: dict[str, float] = {}
    for stage in sleep_stages:
        if stage.get("stage_type") != "awake":
            continue
        session_key = stage.get("session_key")
        if not session_key:
            continue
        duration = float(stage.get("duration_seconds") or 0.0) / 60.0
        awake_by_session[session_key] = awake_by_session.get(session_key, 0.0) + duration
    annotated: list[dict] = []
    for session in sleep_sessions:
        session_key = session.get("session_key")
        annotated.append(
            {
                **session,
                "awake_minutes": awake_by_session.get(session_key, 0.0),
            }
        )
    return annotated


def summarize_context(context: RuleContext, *, window: WindowSpec) -> dict:
    """Render a context block suitable for inclusion in a weekly summary.

    This is the public surface the build-#4 weekly summary
    consumes — the same block the rules use, denormalized into a
    summary-friendly shape.
    """
    return {
        "data_window": {
            "start": window.iso_start,
            "end": window.iso_end,
        },
        "illness_marker_in_window": context.illness_marker_in_window,
        "workout_count_in_window": len(context.workouts_in_window),
        "workout_minutes_in_window": sum(
            float(w.get("duration_minutes") or 0.0)
            for w in context.workouts_in_window
        ),
        "rhr_samples": {
            "current": len(context.rhr_current),
            "baseline": len(context.rhr_baseline),
        },
        "sleep_samples": {
            "recent_week": len(context.sleep_minutes_recent_week),
            "prior_week": len(context.sleep_minutes_prior_week),
        },
        "steps_samples": {
            "current_days": len(context.steps_current_daily),
            "baseline_days": len(context.steps_baseline_daily),
        },
    }


@dataclass(frozen=True)
class SleepNightRecord:
    """One night of sleep, normalized to ``(date, minutes)``.

    HealthQuery returns ``start_time`` (a full ISO timestamp) for
    each sleep session; this helper collapses it to the calendar
    date in UTC, which is the only granularity the sleep rule
    needs.
    """

    date: str
    minutes: float

    @classmethod
    def from_session(cls, session: dict) -> "SleepNightRecord":
        minutes = float(session.get("duration_minutes") or 0.0)
        return cls(date=iso_date(_parse_start(session)), minutes=minutes)


def _parse_start(session: dict) -> "datetime.datetime":  # type: ignore[name-defined]
    import datetime as _dt

    raw = (session.get("start_time") or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)
