from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport
import routers.reports as reports_router

from app_settings import AppSettings
from auth import require_ingest_auth, require_read_auth
from db.database import execute, fetch_all, fetch_one, init_db
from main import app
from main import warn_on_placeholder_tokens
from services.config_store import get_config_value, set_config_value
from services.ingest import ingest_health_payload
from services.operational_settings import OperationalSettings, load_operational_settings, save_operational_settings
from services.seed_data import load_sample_health_payload, seed_sample_health_data


@pytest.mark.asyncio
async def test_health_check_returns_ok():
    await init_db()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_status_endpoint_requires_read_auth():
    await init_db()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/status")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_read_routes_require_auth():
    await init_db()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in (
            "/api/health/overview",
            "/api/health/activity",
            "/api/health/sleep",
            "/api/health/vitals",
            "/api/health/body",
            "/api/health/timeline",
            "/api/health/query",
            "/api/reports/doctor-visit",
            "/api/reports/ask",
        ):
            response = await client.get(path) if path != "/api/health/query" else await client.post(path, json={"sql": "SELECT 1"})
            if path == "/api/reports/doctor-visit":
                response = await client.post(path, json={"start_date": "2026-06-09", "end_date": "2026-06-11", "stream": False})
            elif path == "/api/reports/ask":
                response = await client.post(path, json={"question": "How did my sleep look?", "start_date": "2026-06-09", "end_date": "2026-06-11"})
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_endpoint_returns_counts_with_auth():
    await seed_sample_health_data()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer read-token"},
    ) as client:
        response = await client.get("/api/health/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "metric_points" in body["counts"]


@pytest.mark.asyncio
async def test_schema_version_and_metric_points_table_exist():
    await init_db()
    assert (await fetch_one("SELECT schema_version FROM schema_meta WHERE id = 1"))["schema_version"] == 2
    assert await fetch_one("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'metric_points'") is not None


@pytest.mark.asyncio
async def test_config_store_round_trip():
    await init_db()
    await set_config_value("demo", {"enabled": True})
    assert await get_config_value("demo") == {"enabled": True}


@pytest.mark.asyncio
async def test_operational_settings_round_trip():
    await init_db()
    settings = OperationalSettings(
        stale_sync_threshold_minutes=42,
        report_window_days=3,
        timeline_window_days=9,
        report_disclaimer="Custom disclaimer",
    )
    await save_operational_settings(settings)
    loaded = await load_operational_settings()
    assert loaded == settings


@pytest.mark.asyncio
async def test_ingest_and_read_auth_separate_tokens(monkeypatch):
    monkeypatch.setenv("HEALTHQUERY_INGEST_TOKEN", "ingest-token")
    monkeypatch.setenv("HEALTHQUERY_READ_TOKEN", "read-token")

    await require_ingest_auth(authorization="Bearer ingest-token", request=None)  # type: ignore[arg-type]
    await require_read_auth(authorization="Bearer read-token")

    with pytest.raises(HTTPException):
        await require_ingest_auth(authorization="Bearer read-token", request=None)  # type: ignore[arg-type]

    with pytest.raises(HTTPException):
        await require_read_auth(authorization="Bearer ingest-token")


@pytest.mark.asyncio
async def test_ingest_accepts_alternate_header_and_materializes_data():
    await init_db()
    payload = load_sample_health_payload()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Webhook-Token": "ingest-token"},
    ) as client:
        response = await client.post("/api/webhook/health", content=json.dumps(payload))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["processed"] > 0
    assert body["inserted"] > 0
    assert body["updated"] == 0
    assert body["skipped"] == 0
    assert body["errors"] == []

    batch = await fetch_one("SELECT * FROM ingest_batches ORDER BY received_at DESC LIMIT 1")
    assert batch is not None
    assert batch["payload_json"]
    assert batch["processed_count"] == body["processed"]

    metric_points = await fetch_all("SELECT * FROM metric_points")
    metric_intervals = await fetch_all("SELECT * FROM metric_intervals")
    sleep_sessions = await fetch_all("SELECT * FROM sleep_sessions")
    sleep_stages = await fetch_all("SELECT * FROM sleep_stages")
    workouts = await fetch_all("SELECT * FROM workouts")
    summaries = await fetch_all("SELECT * FROM daily_summaries")

    assert metric_points
    assert metric_intervals
    assert sleep_sessions
    assert sleep_stages
    assert workouts
    assert summaries


@pytest.mark.asyncio
async def test_replaying_same_payload_is_idempotent():
    await init_db()
    payload = load_sample_health_payload()
    transport = ASGITransport(app=app)
    headers = {"X-Webhook-Token": "ingest-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        first = await client.post("/api/webhook/health", content=json.dumps(payload))
        second = await client.post("/api/webhook/health", content=json.dumps(payload))

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["inserted"] > 0
    assert first_body["updated"] == 0
    assert second_body["updated"] > 0
    assert (await fetch_one("SELECT COUNT(*) AS count FROM metric_points"))["count"] == 2
    assert (await fetch_one("SELECT COUNT(*) AS count FROM metric_intervals"))["count"] >= 3
    assert (await fetch_one("SELECT COUNT(*) AS count FROM sleep_sessions"))["count"] == 1
    assert (await fetch_one("SELECT COUNT(*) AS count FROM workouts"))["count"] == 1
    assert (await fetch_one("SELECT steps, sleep_minutes, workouts FROM daily_summaries WHERE summary_date = '2026-06-11'")) == {
        "steps": 8420,
        "sleep_minutes": 0,
        "workouts": 1,
    }
    assert (await fetch_one("SELECT steps, sleep_minutes, workouts FROM daily_summaries WHERE summary_date = '2026-06-10'")) == {
        "steps": 0,
        "sleep_minutes": 470,
        "workouts": 0,
    }


@pytest.mark.asyncio
async def test_real_companion_field_names_ingest_numeric_values():
    await init_db()
    payload = {
        "timestamp": "2026-06-13T08:00:00Z",
        "source": "health_connect",
        "steps": [{"start_time": "2026-06-13T00:00:00Z", "end_time": "2026-06-13T23:59:59Z", "count": 12345}],
        "distance": [{"start_time": "2026-06-13T00:00:00Z", "end_time": "2026-06-13T23:59:59Z", "meters": 8123.4}],
        "total_calories": [{"start_time": "2026-06-13T00:00:00Z", "end_time": "2026-06-13T23:59:59Z", "calories": 2410}],
        "heart_rate": [{"time": "2026-06-13T07:00:00Z", "bpm": 71}],
        "resting_heart_rate": [{"time": "2026-06-13T07:05:00Z", "bpm": 58}],
        "oxygen_saturation": [{"time": "2026-06-13T07:10:00Z", "percentage": 97}],
        "sleep": [
            {
                "session_end_time": "2026-06-13T06:30:00Z",
                "duration_seconds": 27000,
                "stages": [
                    {
                        "stage": "deep",
                        "start_time": "2026-06-13T01:00:00Z",
                        "end_time": "2026-06-13T02:00:00Z",
                        "duration_seconds": 3600,
                    }
                ],
            }
        ],
    }

    result = await ingest_health_payload(payload)

    assert result["errors"] == []
    steps = await fetch_one("SELECT numeric_value, unit FROM metric_intervals WHERE metric_type = 'steps'")
    distance = await fetch_one("SELECT numeric_value, unit FROM metric_intervals WHERE metric_type = 'distance'")
    calories = await fetch_one("SELECT numeric_value, unit FROM metric_intervals WHERE metric_type = 'total_calories'")
    resting_hr = await fetch_one("SELECT numeric_value, unit FROM metric_points WHERE metric_type = 'resting_heart_rate'")
    spo2 = await fetch_one("SELECT numeric_value, unit FROM metric_points WHERE metric_type = 'oxygen_saturation'")
    sleep = await fetch_one("SELECT start_time, end_time, duration_minutes FROM sleep_sessions")
    stage = await fetch_one("SELECT stage_type, duration_seconds FROM sleep_stages")
    summary = await fetch_one("SELECT steps FROM daily_summaries WHERE summary_date = '2026-06-13'")

    assert steps == {"numeric_value": 12345.0, "unit": "count"}
    assert distance == {"numeric_value": 8123.4, "unit": "m"}
    assert calories == {"numeric_value": 2410.0, "unit": "kcal"}
    assert resting_hr == {"numeric_value": 58.0, "unit": "bpm"}
    assert spo2 == {"numeric_value": 97.0, "unit": "%"}
    assert sleep == {
        "start_time": "2026-06-12T23:00:00Z",
        "end_time": "2026-06-13T06:30:00Z",
        "duration_minutes": 450.0,
    }
    assert stage == {"stage_type": "deep", "duration_seconds": 3600.0}
    assert summary == {"steps": 12345}


@pytest.mark.asyncio
async def test_seed_sample_health_data_helper():
    await init_db()
    result = await seed_sample_health_data()
    assert result["processed"] > 0
    assert load_sample_health_payload()["source"] == "health_connect"


@pytest.mark.asyncio
async def test_read_views_and_sql_query_are_available_after_ingest():
    await seed_sample_health_data()
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer read-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        overview = await client.get("/api/health/overview")
        activity = await client.get("/api/health/activity")
        sleep = await client.get("/api/health/sleep")
        vitals = await client.get("/api/health/vitals")
        body = await client.get("/api/health/body")
        timeline = await client.get("/api/health/timeline")
        batches = await client.get("/api/health/batches")
        query = await client.post("/api/health/query", json={"sql": "SELECT COUNT(*) AS count FROM workouts"})

    assert overview.status_code == 200
    assert activity.status_code == 200
    assert sleep.status_code == 200
    assert vitals.status_code == 200
    assert body.status_code == 200
    assert timeline.status_code == 200
    assert batches.status_code == 200
    assert query.status_code == 200

    overview_body = overview.json()
    assert "cards" in overview_body
    assert "daily_summaries" in overview_body

    timeline_body = timeline.json()
    assert timeline_body["days"] == 14
    assert timeline_body["events"]
    first_event = timeline_body["events"][0]
    assert first_event["event_id"]
    assert first_event["category"]
    assert first_event["event_time"]
    assert first_event["title"]
    assert first_event["id"]
    assert first_event["timestamp"]
    assert first_event["summary"]
    assert first_event["metrics"]
    assert first_event["record_key"]
    assert first_event["data_quality"] in {"measured", "estimated"}
    assert isinstance(first_event["detail_json"], dict)

    batches_body = batches.json()
    assert batches_body["batches"]
    assert "payload_json" in batches_body["batches"][0]
    assert batches_body["batches"][0]["batch_id"]

    query_body = query.json()
    assert query_body["row_count"] == 1
    assert query_body["rows"][0]["count"] >= 1


@pytest.mark.asyncio
async def test_sql_query_endpoint_enforces_row_and_byte_limits():
    await init_db()
    await execute("DROP TABLE IF EXISTS query_limit_rows")
    await execute("CREATE TABLE query_limit_rows (value INTEGER NOT NULL)")
    for value in range(1, 1201):
        await execute("INSERT INTO query_limit_rows (value) VALUES (?)", (value,))
    transport = ASGITransport(app=app)
    query_sql = "SELECT value FROM query_limit_rows ORDER BY value ASC"
    headers = {"Authorization": "Bearer read-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        response = await client.post("/api/health/query", json={"sql": query_sql})

    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 1200
    assert body["returned_row_count"] == 1000
    assert body["truncated"] is True
    assert body["byte_count"] <= 1_000_000
    assert len(body["rows"]) == 1000


@pytest.mark.asyncio
async def test_startup_warns_on_placeholder_tokens(caplog):
    caplog.set_level("WARNING")
    warn_on_placeholder_tokens(
        AppSettings(
            db_path=Path("data/healthquery.db"),
            ingest_token="change-me-ingest",
            read_token="change-me-read",
            log_level="INFO",
            llm_base_url=None,
            llm_model=None,
            llm_api_key=None,
            llm_timeout_seconds=60.0,
        )
    )

    warnings = [record.message for record in caplog.records if record.levelname == "WARNING"]
    assert any("HEALTHQUERY_INGEST_TOKEN is still set to a placeholder value" in message for message in warnings)
    assert any("HEALTHQUERY_READ_TOKEN is still set to a placeholder value" in message for message in warnings)


@pytest.mark.asyncio
async def test_report_generation_and_ask_endpoints_work():
    await seed_sample_health_data()
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer read-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        report = await client.post(
            "/api/reports/doctor-visit",
            json={"start_date": "2026-06-09", "end_date": "2026-06-11", "stream": False},
        )
        ask = await client.post(
            "/api/reports/ask",
            json={"question": "How did my sleep and activity look?", "start_date": "2026-06-09", "end_date": "2026-06-11"},
        )

    assert report.status_code == 200
    report_body = report.json()
    assert report_body["status"] == "success"
    assert report_body["report"]["report_type"] == "doctor_visit"
    assert report_body["report"]["narrative"]
    assert "non-diagnostic" in report_body["report"]["disclaimer"].lower()
    assert ask.status_code == 200
    ask_body = ask.json()
    assert ask_body["status"] == "success"
    assert ask_body["answer"]
    assert ask_body["evidence"]


@pytest.mark.asyncio
async def test_report_streaming_uses_sse_when_llm_enabled(monkeypatch):
    await seed_sample_health_data()
    monkeypatch.setattr(reports_router, "llm_is_configured", lambda: True)

    async def fake_rewrite(prompt: str) -> str:
        return "LLM narrative"

    monkeypatch.setattr(reports_router, "maybe_rewrite_with_llm", fake_rewrite)

    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer read-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        response = await client.post(
            "/api/reports/doctor-visit",
            json={"start_date": "2026-06-09", "end_date": "2026-06-11", "stream": True},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: token" in response.text
    assert "LLM narrative" in response.text
    assert "event: done" in response.text


@pytest.mark.asyncio
async def test_timeline_days_filter_changes_result_set():
    await seed_sample_health_data()
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer read-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        short = await client.get("/api/health/timeline?days=1")
        long = await client.get("/api/health/timeline?days=14")

    assert short.status_code == 200
    assert long.status_code == 200
    assert len(long.json()["events"]) >= len(short.json()["events"])


@pytest.mark.asyncio
async def test_sql_guard_rejects_mutations_and_bad_statements():
    await init_db()
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer read-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        bad = await client.post("/api/health/query", json={"sql": "ATTACH DATABASE 'x' AS y"})
        multi = await client.post("/api/health/query", json={"sql": "SELECT 1; SELECT 2"})
        pragma = await client.post("/api/health/query", json={"sql": "PRAGMA journal_mode"})

    assert bad.status_code == 400
    assert multi.status_code == 400
    assert pragma.status_code == 400
