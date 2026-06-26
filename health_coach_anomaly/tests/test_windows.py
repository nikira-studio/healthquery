"""Unit tests for :mod:`health_coach_anomaly.windows`.

Date math is the foundation of every rule; these tests cover
window/baseline construction, percent-change arithmetic, and
the half-open ``[start, end)`` invariant the detector depends on.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from health_coach_anomaly.windows import (
    WindowSpec,
    build_window,
    daily_mean,
    iso_date,
    percent_change,
    _parse_iso_utc,
)


# ---------------------------------------------------------------------------
# WindowSpec
# ---------------------------------------------------------------------------


def test_window_spec_requires_timezone_aware():
    naive = _dt.datetime(2026, 6, 26, tzinfo=None)
    with pytest.raises(ValueError):
        WindowSpec(start=naive, end=naive.replace(hour=12))


def test_window_spec_rejects_zero_or_inverted_range():
    t0 = _dt.datetime(2026, 6, 26, tzinfo=_dt.timezone.utc)
    with pytest.raises(ValueError):
        WindowSpec(start=t0, end=t0)
    with pytest.raises(ValueError):
        WindowSpec(start=t0, end=t0 - _dt.timedelta(hours=1))


def test_window_spec_days_property_floors():
    start = _dt.datetime(2026, 6, 19, 0, 0, 0, tzinfo=_dt.timezone.utc)
    end = start + _dt.timedelta(days=7, hours=3)  # 7.125 days
    spec = WindowSpec(start=start, end=end)
    assert spec.days == 7


def test_window_spec_contains_is_half_open():
    start = _dt.datetime(2026, 6, 19, tzinfo=_dt.timezone.utc)
    end = _dt.datetime(2026, 6, 26, tzinfo=_dt.timezone.utc)
    spec = WindowSpec(start=start, end=end)
    assert spec.contains(start)
    assert not spec.contains(end)  # end is exclusive
    assert spec.contains(end - _dt.timedelta(seconds=1))


def test_window_spec_contains_iso_handles_z_suffix():
    start = _dt.datetime(2026, 6, 19, tzinfo=_dt.timezone.utc)
    end = _dt.datetime(2026, 6, 26, tzinfo=_dt.timezone.utc)
    spec = WindowSpec(start=start, end=end)
    assert spec.contains_iso("2026-06-19T00:00:00Z")
    assert not spec.contains_iso("2026-06-26T00:00:00Z")


def test_window_spec_iso_uses_z_suffix():
    start = _dt.datetime(2026, 6, 19, 0, 0, 0, tzinfo=_dt.timezone.utc)
    end = _dt.datetime(2026, 6, 26, 0, 0, 0, tzinfo=_dt.timezone.utc)
    spec = WindowSpec(start=start, end=end)
    assert spec.iso_start == "2026-06-19T00:00:00Z"
    assert spec.iso_end == "2026-06-26T00:00:00Z"


# ---------------------------------------------------------------------------
# build_window
# ---------------------------------------------------------------------------


def test_build_window_yields_adjacent_non_overlapping_pair():
    now = _dt.datetime(2026, 6, 26, 8, 0, 0, tzinfo=_dt.timezone.utc)
    current, baseline = build_window(now=now, window_days=7, baseline_days=28)
    assert current.end == now
    assert current.start == now - _dt.timedelta(days=7)
    assert baseline.end == current.start  # no overlap
    assert baseline.start == current.start - _dt.timedelta(days=28)
    assert current.days == 7
    assert baseline.days == 28


def test_build_window_rejects_non_positive_inputs():
    now = _dt.datetime(2026, 6, 26, tzinfo=_dt.timezone.utc)
    with pytest.raises(ValueError):
        build_window(now=now, window_days=0, baseline_days=28)
    with pytest.raises(ValueError):
        build_window(now=now, window_days=7, baseline_days=0)


# ---------------------------------------------------------------------------
# percent_change
# ---------------------------------------------------------------------------


def test_percent_change_positive_rise():
    assert percent_change(110.0, 100.0) == pytest.approx(0.10)


def test_percent_change_negative_drop():
    assert percent_change(85.0, 100.0) == pytest.approx(-0.15)


def test_percent_change_zero_baseline_is_none():
    """Division by zero is undefined; return None so callers can branch."""
    assert percent_change(50.0, 0.0) is None
    assert percent_change(-5.0, 0.0) is None


# ---------------------------------------------------------------------------
# daily_mean
# ---------------------------------------------------------------------------


def test_daily_mean_empty_returns_none():
    assert daily_mean([]) is None


def test_daily_mean_simple_average():
    assert daily_mean([10.0, 20.0, 30.0]) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# iso helpers
# ---------------------------------------------------------------------------


def test_iso_date_renders_utc_yyyy_mm_dd():
    t = _dt.datetime(2026, 6, 26, 23, 30, 0, tzinfo=_dt.timezone.utc)
    assert iso_date(t) == "2026-06-26"


def test_iso_date_handles_offset():
    """A non-UTC datetime should render its UTC date, not its local date."""
    tz = _dt.timezone(_dt.timedelta(hours=-5))
    local = _dt.datetime(2026, 6, 26, 22, 0, 0, tzinfo=tz)  # 03:00 UTC next day
    assert iso_date(local) == "2026-06-27"


def test_parse_iso_utc_handles_z_suffix():
    parsed = _parse_iso_utc("2026-06-26T08:00:00Z")
    assert parsed == _dt.datetime(2026, 6, 26, 8, 0, 0, tzinfo=_dt.timezone.utc)


def test_parse_iso_utc_handles_offset():
    parsed = _parse_iso_utc("2026-06-26T08:00:00+00:00")
    assert parsed == _dt.datetime(2026, 6, 26, 8, 0, 0, tzinfo=_dt.timezone.utc)


def test_parse_iso_utc_rejects_empty_string():
    with pytest.raises(ValueError):
        _parse_iso_utc("")


def test_parse_iso_utc_rejects_non_string():
    with pytest.raises(TypeError):
        _parse_iso_utc(12345)
