from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from bot.json_store import load_json, save_json


def _default_data() -> Dict[str, Any]:
    return {"teams": {}}


@dataclass
class CampfireState:
    fuel_points: int = 0
    is_camping: bool = False
    channel_id: int | None = None
    started_at: float | None = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CampfireState":
        return cls(
            fuel_points=int(data.get("fuel_points", 0)),
            is_camping=bool(data.get("is_camping", False)),
            channel_id=int(data["channel_id"]) if data.get("channel_id") is not None else None,
            started_at=float(data["started_at"]) if data.get("started_at") is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fuel_points": self.fuel_points,
            "is_camping": self.is_camping,
            "channel_id": self.channel_id,
            "started_at": self.started_at,
        }


def load_campfire(path: Path) -> Dict[int, CampfireState]:
    raw = load_json(path, _default_data()) or _default_data()
    teams_raw = raw.get("teams") or {}
    states: Dict[int, CampfireState] = {}
    for role_id, state_raw in teams_raw.items():
        try:
            states[int(role_id)] = CampfireState.from_dict(state_raw)
        except Exception:
            continue
    return states


def save_campfire(path: Path, states: Dict[int, CampfireState]) -> None:
    payload = {"teams": {str(role_id): state.to_dict() for role_id, state in states.items()}}
    save_json(path, payload)
