from __future__ import annotations

from pathlib import Path
from typing import Dict

from bot.json_store import load_json, save_json

COUNTER_FILENAME = "counters.json"


def _load_counters(path: Path) -> Dict[str, int]:
    return load_json(path, {})


def increment_counter(path: Path, guild_id: int) -> int:
    counters = _load_counters(path)
    key = str(guild_id)
    counters[key] = counters.get(key, 0) + 1
    save_json(path, counters)
    return counters[key]


def get_counter(path: Path, guild_id: int) -> int:
    counters = _load_counters(path)
    return counters.get(str(guild_id), 0)
