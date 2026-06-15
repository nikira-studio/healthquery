from __future__ import annotations

import json
from typing import Any

from db.database import execute, fetch_all, fetch_one


async def get_config_value(key: str, default: Any = None) -> Any:
    row = await fetch_one("SELECT value_json FROM config WHERE key = ?", (key,))
    if row is None:
        return default
    return json.loads(row["value_json"])


async def set_config_value(key: str, value: Any) -> None:
    value_json = json.dumps(value)
    await execute(
        """
        INSERT INTO config (key, value_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
          value_json = excluded.value_json,
          updated_at = CURRENT_TIMESTAMP
        """,
        (key, value_json),
    )


async def list_config_values() -> list[dict[str, Any]]:
    return await fetch_all(
        """
        SELECT key, value_json, updated_at
        FROM config
        ORDER BY key
        """
    )
