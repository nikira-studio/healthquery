"""Window/baseline date math and small numeric helpers.

These helpers are deliberately dependency-light (stdlib only) so they
can be unit-tested without HealthQuery or a running network. The
detector composes them with the live data fetched via the
``healthquery_client`` package.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowSpec:
    """A half-open ``[start, end)`` window in UTC.

    ``start`` and ``end`` are :class:`datetime.datetime` objects in
    UTC. The window is half-open so adjacent windows do not overlap
    when chained (e.g. baseline → current).
    """

    start: _dt.datetime
    end: _dt.datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("WindowSpec start/end must be timezone-aware UTC")
        if self.start >= self.end:
            raise ValueError(
                f"WindowSpec start ({self.start.isoformat()}) must be "
                f"strictly before end ({self.end.isoformat()})"
            )

    @property
    def days(self) -> int:
        """Number of whole days covered (floor of the duration)."""
        return int((self.end - self.start).total_seconds() // 86400)

    @property
    def iso_start(self) -> str:
        return self.start.isoformat().replace("+00:00", "Z")

    @property
    def iso_end(self) -> str:
        return self.end.isoformat().replace("+00:00", "Z")

    def contains(self, ts: _dt.datetime) -> bool:
        return self.start <= ts < self.end

    def contains_iso(self, iso_ts: str) -> bool:
        """Inclusive check against an ISO 8601 timestamp string."""
        parsed = _parse_iso_utc(iso_ts)
        return self.contains(parsed)


def now_utc() -> _dt.datetime:
    """Return the current UTC time, timezone-aware, with second precision."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def build_window(
    *,
    now: _dt.datetime,
    window_days: int,
    baseline_days: int,
) -> tuple[WindowSpec, WindowSpec]:
    """Build a (current, baseline) pair of half-open UTC windows.

    The current window is the most recent ``window_days`` days up to
    ``now``; the baseline window is the ``baseline_days`` days
    immediately preceding the current window. The two windows do not
    overlap, so the same observation cannot be counted twice.
    """
    if window_days <= 0:
        raise ValueError("window_days must be > 0")
    if baseline_days <= 0:
        raise ValueError("baseline_days must be > 0")

    current = WindowSpec(
        start=now - _dt.timedelta(days=window_days),
        end=now,
    )
    baseline = WindowSpec(
        start=current.start - _dt.timedelta(days=baseline_days),
        end=current.start,
    )
    return current, baseline


def percent_change(current_value: float, baseline_value: float) -> float | None:
    """Return ``(current - baseline) / baseline`` as a fraction.

    Returns ``None`` if the baseline is zero (undefined / division by
    zero) so callers can distinguish "no change" from "no data".
    Negative fractions mean the metric dropped; positive mean it rose.
    """
    if baseline_value == 0:
        return None
    return (current_value - baseline_value) / baseline_value


def daily_mean(values: list[float]) -> float | None:
    """Mean of ``values``; ``None`` when the list is empty."""
    if not values:
        return None
    return statistics.fmean(values)


def iso_date(ts: _dt.datetime) -> str:
    """Render a timezone-aware datetime as a YYYY-MM-DD UTC date string."""
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d")


def _parse_iso_utc(value: str) -> _dt.datetime:
    """Parse a HealthQuery-style ISO 8601 timestamp to a tz-aware UTC datetime.

    Accepts the ``...Z`` suffix (which :func:`datetime.fromisoformat`
    rejects in Python < 3.11) and a trailing ``+00:00``. Returns a
    timezone-aware datetime in UTC.
    """
    if not isinstance(value, str):
        raise TypeError(f"expected ISO 8601 string, got {type(value).__name__}")
    raw = value.strip()
    if not raw:
        raise ValueError("empty ISO 8601 string")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)
