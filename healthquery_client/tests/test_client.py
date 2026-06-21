from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    monkeypatch.delenv("HEALTHQUERY_BASE_URL", raising=False)
    monkeypatch.delenv("HEALTHQUERY_READ_TOKEN", raising=False)


def _transport(handler):
    import httpx

    return httpx.MockTransport(handler)


def _ok(payload):
    import httpx

    return httpx.Response(200, json=payload)


def _json(status_code: int, payload):
    import httpx

    return httpx.Response(status_code, json=payload)


def _text(status_code: int, body: str):
    import httpx

    return httpx.Response(status_code, text=body, headers={"content-type": "text/plain"})


from healthquery_client import (  # noqa: E402  (path setup above)
    AsyncHealthQueryClient,
    HealthQueryAPIError,
    HealthQueryAuthError,
    HealthQueryClient,
    HealthQuerySQLGuardError,
    HealthQueryTransportError,
    HealthStatus,
    TimelineEvent,
)


# --- construction ----------------------------------------------------------


def test_sync_client_requires_token(monkeypatch):
    monkeypatch.delenv("HEALTHQUERY_READ_TOKEN", raising=False)
    with pytest.raises(HealthQueryAuthError):
        HealthQueryClient(base_url="http://example")


def test_sync_client_reads_token_from_env(monkeypatch):
    monkeypatch.setenv("HEALTHQUERY_READ_TOKEN", "env-token")
    monkeypatch.setenv("HEALTHQUERY_BASE_URL", "http://envhost:3136")
    seen: dict[str, str] = {}

    def handler(request: "httpx.Request"):
        seen["auth"] = request.headers.get("authorization", "")
        seen["host"] = request.url.host
        return _ok({"status": "ok", "counts": {}, "last_sync_at": None})

    import httpx

    with httpx.MockTransport(handler) as transport:
        client = HealthQueryClient(transport=transport)
        client.get_status()

    assert seen["auth"] == "Bearer env-token"
    assert seen["host"] == "envhost"


def test_async_client_accepts_injected_async_transport():
    import httpx

    def handler(request: "httpx.Request"):
        assert request.headers.get("authorization") == "Bearer abc"
        return _ok({"status": "ok", "counts": {}, "last_sync_at": None})

    transport = httpx.MockTransport(handler)
    client = AsyncHealthQueryClient(
        base_url="http://api.local",
        read_token="abc",
        transport=httpx.MockTransport(handler),
    )
    import asyncio

    result = asyncio.run(client.get_status())
    assert result["status"] == "ok"


# --- error mapping ---------------------------------------------------------


def test_auth_error_on_401():
    def handler(request: "httpx.Request"):
        return _json(401, {"detail": "Not authenticated"})

    with pytest.raises(HealthQueryAuthError):
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.get_status()


def test_auth_error_on_403():
    def handler(request: "httpx.Request"):
        return _json(403, {"detail": "Forbidden"})

    with pytest.raises(HealthQueryAuthError):
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.get_batches()


def test_sql_guard_error_on_400():
    def handler(request: "httpx.Request"):
        return _json(400, {"detail": "SQL guard rejected statement: must be a single SELECT"})

    with pytest.raises(HealthQuerySQLGuardError) as excinfo:
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.post_query("DROP TABLE metric_points")

    assert excinfo.value.status_code == 400


def test_api_error_on_500():
    def handler(request: "httpx.Request"):
        return _json(500, {"detail": "boom"})

    with pytest.raises(HealthQueryAPIError) as excinfo:
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.get_overview()

    assert excinfo.value.status_code == 500


def test_transport_error_on_connect_failure():
    import httpx

    def handler(request: "httpx.Request"):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(HealthQueryTransportError):
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.get_status()


def test_api_error_on_non_json_error_body():
    # A 5xx with a non-JSON body is still an api error (the server responded,
    # just without structured detail). Surface it as HealthQueryAPIError so
    # callers can read the status code and raw text.
    def handler(request: "httpx.Request"):
        return _text(502, "<html>bad gateway</html>")

    with pytest.raises(HealthQueryAPIError) as excinfo:
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.get_status()

    assert excinfo.value.status_code == 502


# --- happy paths -----------------------------------------------------------


def test_status_endpoint_returns_parsed_json():
    def handler(request: "httpx.Request"):
        assert request.url.path == "/api/health/status"
        return _ok(
            {
                "status": "ok",
                "last_sync_at": "2026-06-20T21:38:22.457385Z",
                "counts": {"ingest_batches": 159, "sleep_sessions": 17},
            }
        )

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        raw = c.get_status()

    assert raw["status"] == "ok"
    assert raw["counts"]["ingest_batches"] == 159


def test_timeline_passes_days_query_param():
    seen: dict[str, str] = {}

    def handler(request: "httpx.Request"):
        seen["query"] = str(request.url.params)
        return _ok({"events": [], "days": 7})

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        c.get_timeline(days=7)

    assert "days=7" in seen["query"]


def test_query_posts_json_body():
    seen: dict[str, object] = {}

    def handler(request: "httpx.Request"):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return _ok(
            {
                "sql": "SELECT 1",
                "row_count": 1,
                "returned_row_count": 1,
                "byte_count": 6,
                "truncated": False,
                "rows": [{"x": 1}],
            }
        )

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        c.post_query("SELECT 1")

    assert seen["path"] == "/api/health/query"
    assert seen["body"] == {"sql": "SELECT 1"}


def test_doctor_visit_report_passes_dates():
    seen: dict[str, object] = {}

    def handler(request: "httpx.Request"):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return _ok({"title": "Q2 summary", "sections": [], "disclaimer": "..."})

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        c.generate_doctor_visit_report(start_date="2026-06-01", end_date="2026-06-20")

    assert seen["path"] == "/api/reports/doctor-visit"
    body = seen["body"]
    assert body["start_date"] == "2026-06-01"
    assert body["end_date"] == "2026-06-20"
    assert body["stream"] is False


def test_doctor_visit_report_omits_none_dates():
    seen: dict[str, object] = {}

    def handler(request: "httpx.Request"):
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return _ok({"title": "All-time summary", "sections": [], "disclaimer": "..."})

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        c.generate_doctor_visit_report()

    assert "start_date" not in seen["body"]
    assert "end_date" not in seen["body"]


def test_ask_health_question_passes_question_and_dates():
    seen: dict[str, object] = {}

    def handler(request: "httpx.Request"):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return _ok({"answer": "summary", "model": None})

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        c.ask_health_question(
            "How did my sleep change this week?",
            start_date="2026-06-14",
            end_date="2026-06-20",
        )

    assert seen["path"] == "/api/reports/ask"
    body = seen["body"]
    assert body["question"].startswith("How did")
    assert body["start_date"] == "2026-06-14"


# --- model validation ------------------------------------------------------


def test_health_status_model_validates_minimum_payload():
    status = HealthStatus.model_validate({"status": "ok"})
    assert status.status == "ok"
    assert status.last_sync_at is None
    assert status.counts == {}


def test_health_status_model_accepts_extra_counts_keys():
    payload = {
        "status": "ok",
        "last_sync_at": "2026-06-20T00:00:00Z",
        "counts": {
            "ingest_batches": 159,
            "metric_intervals": 1661,
            "metric_points": 5265,
            "sleep_sessions": 17,
            "sleep_stages": 443,
            "workouts": 3,
        },
    }
    status = HealthStatus.model_validate(payload)
    assert status.counts["ingest_batches"] == 159


def test_timeline_event_model_round_trips():
    payload = {
        "id": "evt-1",
        "timestamp": "2026-06-20T07:00:00Z",
        "start_time": "2026-06-20T06:45:00Z",
        "end_time": "2026-06-20T07:15:00Z",
        "category": "sleep",
        "type": "sleep_session",
        "title": "Sleep",
        "summary": "30 min nap",
        "metrics": {"duration_minutes": 30},
        "source": "health_connect",
        "record_key": "sleep:2026-06-20T06:45:00Z",
        "data_quality": "measured",
    }
    event = TimelineEvent.model_validate(payload)
    dumped = event.model_dump()
    assert dumped["id"] == "evt-1"
    assert dumped["category"] == "sleep"
    assert dumped["metrics"]["duration_minutes"] == 30