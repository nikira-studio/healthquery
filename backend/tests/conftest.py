from __future__ import annotations

import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def isolated_healthquery_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "healthquery.db"))
    monkeypatch.setenv("HEALTHQUERY_INGEST_TOKEN", "ingest-token")
    monkeypatch.setenv("HEALTHQUERY_READ_TOKEN", "read-token")
    monkeypatch.setenv("HEALTHQUERY_LOG_LEVEL", "INFO")
    monkeypatch.setenv("HEALTHQUERY_AUTH_HEADER", "X-Webhook-Token")
