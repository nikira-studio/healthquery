from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from services.config_store import get_config_value, set_config_value

OPERATIONAL_SETTINGS_KEY = "operational_settings"


@dataclass(frozen=True)
class OperationalSettings:
    stale_sync_threshold_minutes: int = 180
    report_window_days: int = 7
    timeline_window_days: int = 14
    report_disclaimer: str = (
        "This is a non-diagnostic summary of trends from your own health data."
    )
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: int = 60


async def load_operational_settings() -> OperationalSettings:
    raw = await get_config_value(OPERATIONAL_SETTINGS_KEY, default={})
    if not isinstance(raw, dict):
        raw = {}
    defaults = asdict(OperationalSettings())
    valid_keys = set(defaults.keys())
    merged: dict[str, Any] = {**defaults, **{k: v for k, v in raw.items() if k in valid_keys}}
    return OperationalSettings(**merged)


async def save_operational_settings(settings: OperationalSettings) -> None:
    await set_config_value(OPERATIONAL_SETTINGS_KEY, asdict(settings))
