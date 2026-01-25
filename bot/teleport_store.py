from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.json_store import load_json, save_json


def _default_data() -> Dict[str, object]:
    return {"triggers": {}, "pings": []}


@dataclass
class TeleportTrigger:
    trigger: str
    target_channel_id: int
    parent_channel_id: Optional[int]

    @classmethod
    def from_dict(cls, key: str, data: Dict[str, Any]) -> "TeleportTrigger":
        return cls(
            trigger=key,
            target_channel_id=int(data["target_channel_id"]),
            parent_channel_id=int(data["parent_channel_id"]) if data.get("parent_channel_id") is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_channel_id": self.target_channel_id,
            "parent_channel_id": self.parent_channel_id,
        }


@dataclass
class TeleportPing:
    guild_id: int
    team_role_id: int
    channel_id: int
    message_id: int
    trigger: str
    timestamp: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TeleportPing":
        return cls(
            guild_id=int(data["guild_id"]),
            team_role_id=int(data["team_role_id"]),
            channel_id=int(data["channel_id"]),
            message_id=int(data["message_id"]),
            trigger=str(data.get("trigger", "")),
            timestamp=float(data.get("timestamp", 0)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "team_role_id": self.team_role_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "trigger": self.trigger,
            "timestamp": self.timestamp,
        }


def load_teleport(path: Path) -> Dict[str, Any]:
    raw = load_json(path, _default_data()) or _default_data()
    triggers_raw = raw.get("triggers") or {}
    pings_raw = raw.get("pings") or []

    triggers: Dict[str, TeleportTrigger] = {}
    for key, value in triggers_raw.items():
        try:
            triggers[key] = TeleportTrigger.from_dict(key, value)
        except Exception:
            continue

    pings: List[TeleportPing] = []
    for record in pings_raw:
        try:
            pings.append(TeleportPing.from_dict(record))
        except Exception:
            continue

    return {"triggers": triggers, "pings": pings}


def save_teleport(path: Path, data: Dict[str, Any]) -> None:
    triggers: Dict[str, TeleportTrigger] = data.get("triggers", {})
    pings: List[TeleportPing] = data.get("pings", [])
    payload = {
        "triggers": {key: trigger.to_dict() for key, trigger in triggers.items()},
        "pings": [ping.to_dict() for ping in pings],
    }
    save_json(path, payload)
