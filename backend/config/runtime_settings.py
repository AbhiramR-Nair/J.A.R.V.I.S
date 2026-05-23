"""
Runtime settings: user-mutable preferences written back to data/settings.json.

Separate from settings.py because Pydantic Settings is read-once at boot and
cannot write back. This module handles the mic device choice, which can change
mid-session via the /audio/device endpoint.
"""

import json
from pathlib import Path
from typing import TypedDict

from loguru import logger

from backend.config.settings import get_settings


class DeviceEntry(TypedDict):
    index: int
    name: str


def _settings_path() -> Path:
    return get_settings().runtime_settings_path


def _load() -> dict:
    """Read data/settings.json; return empty dict if file is missing or corrupt."""
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Corrupt file is non-fatal — fall back to defaults and log clearly.
        logger.warning(f"runtime_settings: could not read {path}: {exc}; using defaults")
        return {}


def _save(data: dict) -> None:
    """Write dict to data/settings.json, creating parent dirs if needed."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_input_device() -> DeviceEntry | None:
    """Return the saved mic device dict, or None if no device has been chosen yet."""
    data = _load()
    entry = data.get("input_device")
    if entry and "index" in entry and "name" in entry:
        return DeviceEntry(index=entry["index"], name=entry["name"])
    return None


def set_input_device(index: int, name: str) -> DeviceEntry:
    """Persist the user's mic choice. Returns the saved entry."""
    data = _load()
    entry = DeviceEntry(index=index, name=name)
    data["input_device"] = entry
    _save(data)
    logger.info(f"runtime_settings: input device saved → index={index} name={name!r}")
    return entry
