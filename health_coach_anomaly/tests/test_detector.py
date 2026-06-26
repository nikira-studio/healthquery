"""End-to-end tests for :class:`health_coach_anomaly.detector.AnomalyDetector`.

These tests drive the detector against a mocked HealthQuery
transport so the read path, the rule plumbing, and the
``AnomalyReport`` shape are all exercised without a live api.
The mocked transport is the same shape as the live API: SQL
strings come in via ``POST /api/health/query``, and the
detector expects rows keyed by the same column names HealthQuery
uses (``recorded_at``, ``numeric_value``, ``start_time``,
``duration_minutes``).
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Callable

import httpx
import pytest

from health_coach_anomaly import (
    AnomalyDetector,
    AnomalyReport,
    AnomalySeverity,
    AnomalyStatus,
)
from healthquery_client import HealthQueryClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _result(rows: list[dict]) -> dict:
    return {
        "sql": "",
        "row_count": len(rows),
        "returned_row_count": len(rows),
        "byte_count": 0,
        "truncated": False,
        "rows": rows,
    }


def _fixed_now() -> _dt.datetime:
    """Pin the detector to a known 'now' so tests are deterministic."""
    return _dt.datetime(2026, 6, 26, 8, 0, 0, tzinfo=_dt.timezone.utc)


def _build_client(handler: Callable[[httpx.Request], httpx.Response]) -> HealthQueryClient:
    transport = httpx.MockTransport(handler)
    return HealthQueryClient(
        base_url="http://healthquery-api:3136",
        read_token="test-token",
        transport=transport,
    )


def _batches_handler(batches: list[dict], queries: list[dict]) -> Callable[[httpx.Request], httpx.Response]:
    """Build a transport handler that serves a fixed /batches response and a queue of /query responses.

    Each ``queries`` entry is a dict mapping a substring of the
    expected SQL → the row payload to return. The handler pops
    the first matching entry per call; queries that don't
    match a stub return an empty row set (so the detector
    doesn't crash when, e.g., a HRV probe is sent and no HRV
    is in the live data).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": batches})
        if request.url.path.endswith("/api/health/query"):
            body = json.loads(request.content.decode("utf-8"))
            sql = body.get("sql", "")
            for stub in queries:
                for needle, payload in stub.items():
                    if needle in sql:
                        return _ok(_result(payload))
            return _ok(_result([]))
        return _ok({})

    return handler


# ---------------------------------------------------------------------------
# Acceptance: a known HRV drop fires with a context block
# ---------------------------------------------------------------------------


def test_known_hrv_drop_fires_with_context_block():
    """STA-50 acceptance: HRV drop with a context block."""
    now = _fixed_now()
    baseline = [{"recorded_at": (now - _dt.timedelta(days=15 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 60.0} for i in range(30)]
    current = [{"recorded_at": (now - _dt.timedelta(days=2 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 40.0} for i in range(7)]
    rhr_baseline = [{"recorded_at": (now - _dt.timedelta(days=15 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 60.0} for i in range(30)]
    rhr_current = [{"recorded_at": (now - _dt.timedelta(days=2 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 70.0} for i in range(7)]

    def make_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": [{"batch_id": "batch_test_001"}]})
        if request.url.path.endswith("/api/health/query"):
            body = json.loads(request.content.decode("utf-8"))
            sql = body.get("sql", "")
            if "heart_rate_variability" in sql:
                return _ok(_result(current + baseline))
            if "resting_heart_rate" in sql:
                return _ok(_result(rhr_current + rhr_baseline))
            if "sleep_sessions" in sql:
                return _ok(_result([]))
            if "metric_intervals" in sql:
                return _ok(_result([]))
            return _ok(_result([]))
        return _ok({})

    client = _build_client(make_handler)
    detector = AnomalyDetector(client, now=lambda: now, run_id="test-hrv-drop")
    report = detector.detect(window_days=7, baseline_days=28)

    assert isinstance(report, AnomalyReport)
    assert report.run_id == "test-hrv-drop"
    assert report.healthquery_batch_id == "batch_test_001"
    hrv_fired = next(a for a in report.anomalies if a.rule == "hrv_drop")
    assert hrv_fired.status == AnomalyStatus.FIRED
    # The "context block" is the context dict: it must include the
    # co-movement / illness-marker fields, not just the raw deviation.
    assert "pct_change" in hrv_fired.context
    assert "rhr_co_movement" in hrv_fired.context
    assert "illness_marker_in_window" in hrv_fired.context
    # Co-movement: RHR rose in the same window, so the HRV rule
    # must surface that for the summary.
    assert hrv_fired.context["rhr_co_movement"] > 0


# ---------------------------------------------------------------------------
# Acceptance: no fire when the trend is within threshold
# ---------------------------------------------------------------------------


def test_within_threshold_does_not_fire():
    """STA-50 acceptance: rule does not fire when trend is inside the band."""
    now = _fixed_now()
    baseline = [{"recorded_at": (now - _dt.timedelta(days=15 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 60.0} for i in range(30)]
    current = [{"recorded_at": (now - _dt.timedelta(days=2 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 57.0} for i in range(7)]  # 5% drop

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": []})
        if request.url.path.endswith("/api/health/query"):
            body = json.loads(request.content.decode("utf-8"))
            sql = body.get("sql", "")
            if "heart_rate_variability" in sql:
                return _ok(_result(current + baseline))
            return _ok(_result([]))
        return _ok({})

    client = _build_client(handler)
    detector = AnomalyDetector(client, now=lambda: now, run_id="test-within")
    report = detector.detect(window_days=7, baseline_days=28)

    hrv = next(a for a in report.anomalies if a.rule == "hrv_drop")
    assert hrv.status == AnomalyStatus.WITHIN_THRESHOLD
    assert hrv.severity == AnomalySeverity.INFO
    # Critical: no false-positive spam.
    assert hrv.status != AnomalyStatus.FIRED


# ---------------------------------------------------------------------------
# Acceptance: missing metric returns data_not_available
# ---------------------------------------------------------------------------


def test_missing_metric_returns_data_not_available():
    """STA-50 acceptance: HRV absent → data_not_available, no false flag.

    This is the case the detector must handle today per
    STA-48 §5: HRV has zero rows in the live data. The
    detector must return ``data_not_available`` (not
    ``within_threshold``, not ``fired``) so the summary
    can render "HRV not yet ingested" without alarming
    the operator.
    """
    now = _fixed_now()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": []})
        if request.url.path.endswith("/api/health/query"):
            # All queries return empty rows.
            return _ok(_result([]))
        return _ok({})

    client = _build_client(handler)
    detector = AnomalyDetector(client, now=lambda: now, run_id="test-no-hrv")
    report = detector.detect(window_days=7, baseline_days=28)

    hrv = next(a for a in report.anomalies if a.rule == "hrv_drop")
    assert hrv.status == AnomalyStatus.DATA_NOT_AVAILABLE
    assert "not yet ingested" in hrv.summary.lower()
    # The other rules also report data_not_available.
    rhr = next(a for a in report.anomalies if a.rule == "rhr_rise")
    assert rhr.status == AnomalyStatus.DATA_NOT_AVAILABLE
    steps = next(a for a in report.anomalies if a.rule == "steps_collapse")
    assert steps.status == AnomalyStatus.DATA_NOT_AVAILABLE
    sleep = next(a for a in report.anomalies if a.rule == "sleep_collapse")
    assert sleep.status == AnomalyStatus.DATA_NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Steps collapse with cross-rule co-movement
# ---------------------------------------------------------------------------


def test_steps_collapse_fires_with_illness_marker_promotes_severity():
    """When a steps collapse is paired with an illness marker, severity promotes."""
    now = _fixed_now()
    steps_baseline = [
        {"start_time": (now - _dt.timedelta(days=20 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 1000.0}
        for i in range(28)
    ]
    steps_current = [
        {"start_time": (now - _dt.timedelta(days=2 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 100.0}
        for i in range(7)
    ]
    sleep_sessions = [
        {"session_key": "s1", "start_time": "2026-06-25T22:00:00Z", "end_time": "2026-06-26T06:00:00Z", "duration_minutes": 480.0}
    ]
    sleep_stages = [
        {"session_key": "s1", "stage_type": "awake", "duration_seconds": 120 * 60, "start_time": "2026-06-25T22:00:00Z", "end_time": "2026-06-25T23:00:00Z"}  # 25% awake
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": [{"batch_id": "batch_illness"}]})
        if request.url.path.endswith("/api/health/query"):
            body = json.loads(request.content.decode("utf-8"))
            sql = body.get("sql", "")
            if "metric_intervals" in sql and "steps" in sql:
                return _ok(_result(steps_baseline + steps_current))
            if "FROM sleep_sessions" in sql and "duration_minutes" in sql:
                return _ok(_result(sleep_sessions))
            if "FROM sleep_sessions" in sql:
                return _ok(_result(sleep_sessions))
            if "FROM sleep_stages" in sql:
                return _ok(_result(sleep_stages))
            return _ok(_result([]))
        return _ok({})

    client = _build_client(handler)
    detector = AnomalyDetector(client, now=lambda: now, run_id="test-illness-steps")
    report = detector.detect(window_days=7, baseline_days=28)

    steps = next(a for a in report.anomalies if a.rule == "steps_collapse")
    assert steps.status == AnomalyStatus.FIRED
    assert steps.severity == AnomalySeverity.PROMINENT
    assert steps.context["illness_marker_in_window"] is True


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_detect_is_idempotent_for_same_handler():
    """Re-running detect against the same handler must produce the same output bytes."""
    now = _fixed_now()
    baseline = [{"recorded_at": (now - _dt.timedelta(days=15 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 60.0} for i in range(30)]
    current = [{"recorded_at": (now - _dt.timedelta(days=2 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 40.0} for i in range(7)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": [{"batch_id": "batch_idem"}]})
        if request.url.path.endswith("/api/health/query"):
            body = json.loads(request.content.decode("utf-8"))
            sql = body.get("sql", "")
            if "heart_rate_variability" in sql:
                return _ok(_result(current + baseline))
            return _ok(_result([]))
        return _ok({})

    client1 = _build_client(handler)
    client2 = _build_client(handler)
    detector1 = AnomalyDetector(client1, now=lambda: now, run_id="idem-1")
    detector2 = AnomalyDetector(client2, now=lambda: now, run_id="idem-2")
    r1 = detector1.detect()
    r2 = detector2.detect()
    # run_id differs (per-run unique), but the structural
    # payload (anomalies, threshold snapshot, window) must
    # be byte-identical.
    d1 = r1.to_dict()
    d2 = r2.to_dict()
    d1.pop("run_id")
    d2.pop("run_id")
    d1.pop("generated_at")
    d2.pop("generated_at")
    assert d1 == d2


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def test_render_summary_includes_prominent_and_context():
    now = _fixed_now()
    baseline = [{"recorded_at": (now - _dt.timedelta(days=15 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 60.0} for i in range(30)]
    current = [{"recorded_at": (now - _dt.timedelta(days=2 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "numeric_value": 40.0} for i in range(7)]
    sleep_sessions = [
        {"session_key": "s1", "start_time": "2026-06-25T22:00:00Z", "end_time": "2026-06-26T06:00:00Z", "duration_minutes": 480.0}
    ]
    sleep_stages = [
        {"session_key": "s1", "stage_type": "awake", "duration_seconds": 120 * 60, "start_time": "2026-06-25T22:00:00Z", "end_time": "2026-06-25T23:00:00Z"}
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": [{"batch_id": "batch_render"}]})
        if request.url.path.endswith("/api/health/query"):
            body = json.loads(request.content.decode("utf-8"))
            sql = body.get("sql", "")
            if "heart_rate_variability" in sql:
                return _ok(_result(current + baseline))
            if "FROM sleep_sessions" in sql and "duration_minutes" in sql:
                return _ok(_result(sleep_sessions))
            if "FROM sleep_sessions" in sql:
                return _ok(_result(sleep_sessions))
            if "FROM sleep_stages" in sql:
                return _ok(_result(sleep_stages))
            return _ok(_result([]))
        return _ok({})

    client = _build_client(handler)
    detector = AnomalyDetector(client, now=lambda: now, run_id="test-render")
    report = detector.detect()
    md = detector.render_summary(report)
    assert "HRV" in md
    assert "Prominent" in md or "prominent" in md
    assert "Context block" in md
    assert "batch_render" in md


# ---------------------------------------------------------------------------
# /api/health/batches can be missing or fail
# ---------------------------------------------------------------------------


def test_detect_tolerates_batches_failure():
    """If /api/health/batches fails, the detector still produces a report with batch_id=None."""
    now = _fixed_now()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return httpx.Response(500, json={"detail": "boom"})
        if request.url.path.endswith("/api/health/query"):
            return _ok(_result([]))
        return _ok({})

    client = _build_client(handler)
    detector = AnomalyDetector(client, now=lambda: now, run_id="test-no-batches")
    report = detector.detect()
    assert report.healthquery_batch_id is None
    assert len(report.anomalies) == 4  # all four rules still ran


# ---------------------------------------------------------------------------
# AnomalyReport.to_dict
# ---------------------------------------------------------------------------


def test_to_dict_is_json_serializable():
    """The weekly summary (build #4) will JSON-encode the report; verify it round-trips."""
    now = _fixed_now()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/health/batches"):
            return _ok({"batches": [{"batch_id": "batch_json"}]})
        if request.url.path.endswith("/api/health/query"):
            return _ok(_result([]))
        return _ok({})

    client = _build_client(handler)
    detector = AnomalyDetector(client, now=lambda: now, run_id="test-json")
    report = detector.detect()
    payload = report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["run_id"] == "test-json"
    assert decoded["healthquery_batch_id"] == "batch_json"
    assert len(decoded["anomalies"]) == 4
