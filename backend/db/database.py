from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from app_settings import get_settings
from db.schema_migrations import CURRENT_SCHEMA_VERSION, pending_migrations

SCHEMA_META_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_meta (id, schema_version) VALUES (1, 0);
"""


async def get_connection() -> aiosqlite.Connection:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(settings.db_path.as_posix())
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    return conn


async def _bootstrap_schema_meta(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_META_SQL)
    await conn.commit()


async def _get_schema_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT schema_version FROM schema_meta WHERE id = 1") as cursor:
        row = await cursor.fetchone()
        return int(row["schema_version"]) if row else 0


async def _set_schema_version(conn: aiosqlite.Connection, version: int) -> None:
    await conn.execute(
        """
        UPDATE schema_meta
        SET schema_version = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (version,),
    )


async def init_db() -> None:
    conn = await get_connection()
    try:
        await _bootstrap_schema_meta(conn)
        current_version = await _get_schema_version(conn)
        for version, path in pending_migrations(current_version):
            migration_sql = path.read_text(encoding="utf-8")
            await conn.executescript(migration_sql)
            await _set_schema_version(conn, version)
            await conn.commit()
        if current_version < CURRENT_SCHEMA_VERSION:
            await _set_schema_version(conn, CURRENT_SCHEMA_VERSION)
            await conn.commit()
        await conn.commit()
    finally:
        await conn.close()


async def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    conn = await get_connection()
    try:
        async with conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    finally:
        await conn.close()


async def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = await get_connection()
    try:
        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await conn.close()


async def execute(query: str, params: tuple[Any, ...] = ()) -> int:
    conn = await get_connection()
    try:
        cursor = await conn.execute(query, params)
        await conn.commit()
        return cursor.rowcount
    finally:
        await conn.close()
