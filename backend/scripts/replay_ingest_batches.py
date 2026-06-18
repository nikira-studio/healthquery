from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


DERIVED_TABLES = (
    "metric_points",
    "metric_intervals",
    "sleep_stages",
    "sleep_sessions",
    "workouts",
    "daily_summaries",
    "ingest_batches",
)


async def _load_payloads(db_path: Path) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path.as_posix()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
            SELECT payload_json
            FROM ingest_batches
            WHERE payload_json IS NOT NULL AND payload_json != ''
            ORDER BY received_at ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


async def _clear_derived_tables(db_path: Path) -> None:
    async with aiosqlite.connect(db_path.as_posix()) as conn:
        await conn.execute("PRAGMA foreign_keys=OFF;")
        await conn.execute("BEGIN IMMEDIATE")
        for table in DERIVED_TABLES:
            await conn.execute(f"DELETE FROM {table}")
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=ON;")


async def replay_batches(db_path: Path, apply: bool) -> dict[str, int | str]:
    payloads = await _load_payloads(db_path)
    if not apply:
        return {"mode": "dry_run", "payloads": len(payloads)}

    backup_path = db_path.with_suffix(
        f".backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db"
    )
    shutil.copy2(db_path, backup_path)

    os.environ["DB_PATH"] = db_path.as_posix()
    from db.database import init_db
    from services.ingest import ingest_health_payload

    await init_db()
    await _clear_derived_tables(db_path)
    processed = 0
    for payload in payloads:
        await ingest_health_payload(payload)
        processed += 1
    return {
        "mode": "apply",
        "payloads": len(payloads),
        "replayed": processed,
        "backup_path": backup_path.as_posix(),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Replay stored HealthQuery webhook payloads through the current ingest parser.")
    parser.add_argument("--db-path", default=os.getenv("DB_PATH", "data/healthquery.db"))
    parser.add_argument("--apply", action="store_true", help="Clear derived tables and replay payloads. Without this flag, only prints a dry run.")
    args = parser.parse_args()
    result = await replay_batches(Path(args.db_path), args.apply)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
