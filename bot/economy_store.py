from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set

import discord

from bot.json_store import load_json, save_json


def _default_data() -> Dict[str, object]:
    return {"whitelisted_role_ids": [], "balances": {}}


@dataclass
class EconomyData:
    whitelisted_role_ids: List[str]
    balances: Dict[str, int]

    def to_dict(self) -> Dict[str, object]:
        return {"whitelisted_role_ids": self.whitelisted_role_ids, "balances": self.balances}


def load_economy(path: Path) -> EconomyData:
    raw = load_json(path, _default_data()) or _default_data()
    whitelisted = [str(role_id) for role_id in raw.get("whitelisted_role_ids", [])]
    balances = {str(role_id): int(amount) for role_id, amount in (raw.get("balances") or {}).items()}
    return EconomyData(whitelisted_role_ids=whitelisted, balances=balances)


def save_economy(path: Path, data: EconomyData) -> None:
    save_json(path, data.to_dict())


def get_whitelisted_role_ids(data: EconomyData) -> Set[int]:
    return {int(role_id) for role_id in data.whitelisted_role_ids}


def add_whitelisted_role(data: EconomyData, role_id: int) -> bool:
    role_key = str(role_id)
    if role_key in data.whitelisted_role_ids:
        return False
    data.whitelisted_role_ids.append(role_key)
    data.balances.setdefault(role_key, 0)
    return True


def remove_whitelisted_role(data: EconomyData, role_id: int) -> bool:
    role_key = str(role_id)
    if role_key not in data.whitelisted_role_ids:
        return False
    data.whitelisted_role_ids = [rid for rid in data.whitelisted_role_ids if rid != role_key]
    data.balances.pop(role_key, None)
    return True


def get_balance(data: EconomyData, role_id: int) -> int:
    return data.balances.get(str(role_id), 0)


def set_balance(data: EconomyData, role_id: int, amount: int) -> None:
    data.balances[str(role_id)] = amount


def resolve_member_team_roles(member_roles: List[discord.Role], whitelisted_ids: Set[int]) -> List[discord.Role]:
    return [role for role in member_roles if getattr(role, "id", None) in whitelisted_ids]
