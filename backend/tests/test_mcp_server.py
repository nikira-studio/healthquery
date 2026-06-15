from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from db.database import init_db
from main import app
from services.seed_data import seed_sample_health_data


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def run_backend_server(port: int):
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=1.0) as client:
                    response = await client.get("/api/health")
                    if response.status_code == 200:
                        break
            except httpx.HTTPError:
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError("Backend server did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


@pytest.mark.asyncio
async def test_openapi_schema_hides_webhook_endpoint():
    await init_db()
    schema = app.openapi()
    assert "/api/webhook/health" not in schema["paths"]
    assert "/api/reports/doctor-visit" in schema["paths"]


@pytest.mark.asyncio
async def test_mcp_server_tools_read_from_backend_via_stdio():
    await init_db()
    await seed_sample_health_data()

    backend_port = 3146
    async with run_backend_server(backend_port):
        env = os.environ.copy()
        env["HEALTHQUERY_BASE_URL"] = f"http://127.0.0.1:{backend_port}"
        env["HEALTHQUERY_READ_TOKEN"] = "read-token"

        transport = StdioTransport(
            command=sys.executable,
            args=[str(BACKEND_ROOT / "mcp_server.py")],
            env=env,
            cwd=str(BACKEND_ROOT),
        )
        client = Client(transport)
        async with client:
            tools = await client.list_tools()
            tool_names = {tool.name for tool in tools}
            assert {
                "get_health_overview",
                "get_daily_activity",
                "get_sleep_summary",
                "get_vitals_summary",
                "get_body_summary",
                "get_health_timeline",
                "generate_doctor_visit_report",
                "ask_health_question",
                "execute_health_query",
            }.issubset(tool_names)

            overview = await client.call_tool("get_health_overview", {"days": 7})
            assert overview.data["days"] == 7
            assert "overview" in overview.data
            assert "timeline" in overview.data

            report = await client.call_tool(
                "generate_doctor_visit_report",
                {"start_date": "2026-06-09", "end_date": "2026-06-11"},
            )
            assert report.data["status"] == "success"
            assert report.data["report"]["report_type"] == "doctor_visit"

            query = await client.call_tool(
                "execute_health_query",
                {"sql": "SELECT COUNT(*) AS count FROM workouts"},
            )
            assert query.data["row_count"] == 1
            assert query.data["rows"][0]["count"] >= 1
