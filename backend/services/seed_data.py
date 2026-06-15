from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from db.database import init_db
from services.ingest import ingest_health_payload

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "health_webhook_sample.json"


def load_sample_health_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


async def seed_sample_health_data() -> dict[str, Any]:
    await init_db()
    return await ingest_health_payload(load_sample_health_payload())
