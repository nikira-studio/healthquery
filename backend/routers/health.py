from __future__ import annotations

import time

from fastapi import APIRouter
from db.database import fetch_one

router = APIRouter(prefix="/api", tags=["health"])
START_TIME = time.time()


@router.get("/health", operation_id="get_health_check")
async def get_health_check() -> dict[str, str]:
    row = await fetch_one("SELECT 1 AS ok")
    return {
        "status": "ok" if row else "degraded",
        "database": "connected" if row else "unavailable",
        "version": "0.1.0",
    }
