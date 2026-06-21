from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


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


from healthquery_client import (  # noqa: E402
    AsyncHealthQueryClient,
    HealthQueryAPIError,
    HealthQueryAuthError,
    HealthQueryClient,
    HealthQueryResult,
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
    assert isinstance(result, HealthStatus)
    assert result.status == "ok"


def test_repr_does_not_leak_token():
    client = HealthQueryClient(
        base_url="http://api", read_token="super-secret-token-1234"
    )
    text = repr(client)
    assert "super-secret-token-1234" not in text
    assert "***" in text


def test_invalid_retry_settings_are_rejected():
    with pytest.raises(ValueError):
        HealthQueryClient(base_url="http://api", read_token="t", max_retries=-1)
    with pytest.raises(ValueError):
        HealthQueryClient(
            base_url="http://api", read_token="t", retry_backoff_seconds=-0.1
        )


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


def test_sql_guard_error_on_only_select():
    def handler(request: "httpx.Request"):
        return _json(400, {"detail": "Only SELECT statements are allowed"})

    with pytest.raises(HealthQuerySQLGuardError) as excinfo:
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.post_query("PRAGMA writable_schema = 1")

    assert excinfo.value.status_code == 400


def test_api_error_on_500_after_retries_exhausted():
    def handler(request: "httpx.Request"):
        return _json(500, {"detail": "boom"})

    sleeps: list[float] = []

    with pytest.raises(HealthQueryAPIError) as excinfo:
        with HealthQueryClient(
            base_url="http://x",
            read_token="t",
            transport=_transport(handler),
            retry_sleep=sleeps.append,
            retry_backoff_seconds=0.001,
        ) as c:
            c.get_overview()

    # 3 retries means we expect 4 attempts and 3 sleeps
    assert len(sleeps) == 3
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
    def handler(request: "httpx.Request"):
        return _text(502, "<html>bad gateway</html>")

    with pytest.raises(HealthQueryAPIError) as excinfo:
        with HealthQueryClient(
            base_url="http://x", read_token="t", transport=_transport(handler)
        ) as c:
            c.get_status()

    assert excinfo.value.status_code == 502


# --- retry behavior --------------------------------------------------------


def test_5xx_retries_then_succeeds():
    attempts = {"n": 0}

    def handler(request: "httpx.Request"):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return _json(503, {"detail": "service unavailable"})
        return _ok({"status": "ok", "counts": {}, "last_sync_at": None})

    sleeps: list[float] = []
    with HealthQueryClient(
        base_url="http://x",
        read_token="t",
        transport=_transport(handler),
        retry_sleep=sleeps.append,
        retry_backoff_seconds=0.001,
    ) as c:
        result = c.get_status()

    assert attempts["n"] == 3
    assert len(sleeps) == 2  # 2 retries between 3 attempts
    assert isinstance(result, HealthStatus)
    assert result.status == "ok"


def test_401_does_not_retry():
    attempts = {"n": 0}

    def handler(request: "httpx.Request"):
        attempts["n"] += 1
        return _json(401, {"detail": "Not authenticated"})

    sleeps: list[float] = []
    with pytest.raises(HealthQueryAuthError):
        with HealthQueryClient(
            base_url="http://x",
            read_token="t",
            transport=_transport(handler),
            retry_sleep=sleeps.append,
            retry_backoff_seconds=0.001,
        ) as c:
            c.get_status()

    assert attempts["n"] == 1
    assert sleeps == []


def test_429_does_not_retry():
    """HealthQuery's 429 from rate limiting is not in the 5xx range, so
    the client surfaces it as HealthQueryAPIError immediately."""

    attempts = {"n": 0}

    def handler(request: "httpx.Request"):
        attempts["n"] += 1
        return _json(429, {"detail": "rate limited"})

    with pytest.raises(HealthQueryAPIError) as excinfo:
        with HealthQueryClient(
            base_url="http://x",
            read_token="t",
            transport=_transport(handler),
            retry_sleep=lambda _: None,
            retry_backoff_seconds=0.001,
        ) as c:
            c.get_status()

    assert attempts["n"] == 1
    assert excinfo.value.status_code == 429


def test_backoff_grows_exponentially():
    sleeps: list[float] = []

    def handler(request: "httpx.Request"):
        return _json(500, {"detail": "boom"})

    with pytest.raises(HealthQueryAPIError):
        with HealthQueryClient(
            base_url="http://x",
            read_token="t",
            transport=_transport(handler),
            retry_sleep=sleeps.append,
            retry_backoff_seconds=0.5,
        ) as c:
            c.get_status()

    # Each backoff should be >= base * 2^attempt (ignoring jitter up to 25%).
    assert len(sleeps) == 3
    assert sleeps[0] >= 0.5
    assert sleeps[1] >= 1.0
    assert sleeps[2] >= 2.0


# --- probe (no-auth) -------------------------------------------------------


def test_probe_omits_authorization_header():
    seen: dict[str, str | None] = {}

    def handler(request: "httpx.Request"):
        seen["auth"] = request.headers.get("authorization")
        return _ok({"status": "ok", "database": "ok", "version": "0.1.0"})

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        result = c.probe()

    # probe() must not present a Bearer credential. httpx will deliver
    # either an empty Authorization header (overridden) or no header at
    # all depending on version — neither should carry "Bearer".
    auth = seen["auth"] or ""
    assert not auth.startswith("Bearer ")
    assert result["status"] == "ok"


# --- happy paths -----------------------------------------------------------


def test_get_status_returns_typed_model():
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
        result = c.get_status()

    assert isinstance(result, HealthStatus)
    assert result.status == "ok"
    assert result.counts["ingest_batches"] == 159


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


def test_post_query_returns_typed_model():
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
        result = c.post_query("SELECT 1")

    assert seen["path"] == "/api/health/query"
    assert seen["body"] == {"sql": "SELECT 1"}
    assert isinstance(result, HealthQueryResult)
    assert result.row_count == 1
    assert result.rows == [{"x": 1}]


def test_get_config_returns_typed_model():
    def handler(request: "httpx.Request"):
        return _ok(
            {
                "stale_sync_threshold_minutes": 90,
                "report_window_days": 30,
                "timeline_window_days": 14,
                "report_disclaimer": "...",
                "llm_enabled": True,
                "llm_base_url": "http://x/v1",
                "llm_model": "gpt-4o-mini",
                "llm_api_key_set": True,
            }
        )

    with HealthQueryClient(
        base_url="http://x", read_token="t", transport=_transport(handler)
    ) as c:
        from healthquery_client import ConfigResponse

        cfg = c.get_config()

    assert isinstance(cfg, ConfigResponse)
    assert cfg.llm_enabled is True
    assert cfg.llm_model == "gpt-4o-mini"


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


def test_token_does_not_leak_into_error_messages():
    """If the api somehow echoes the bearer in a 4xx detail, the client
    must scrub it before the exception text leaves the library."""

    secret = "tok-AAA-BBB-CCC-DDD-EEE"

    def handler(request: "httpx.Request"):
        return _json(400, {"detail": f"upstream saw token {secret}"})

    with pytest.raises(HealthQueryAPIError) as excinfo:
        with HealthQueryClient(
            base_url="http://x", read_token=secret, transport=_transport(handler)
        ) as c:
            c.get_overview()

    assert secret not in str(excinfo.value)
    assert secret not in str(excinfo.value.detail)