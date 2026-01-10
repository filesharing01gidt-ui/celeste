from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv


@dataclass
class BotConfig:
    prefix: str
    admin_role_ids: list[int]
    log_level: str
    data_dir: Path
    dev_guild_id: Optional[int]
    token: str


DEFAULT_CONFIG = {
    "prefix": "!",
    "admin_role_ids": [],
    "log_level": "INFO",
    "data_dir": "data",
    "dev_guild_id": None,
}


def _load_yaml_config(config_path: Path) -> dict:
    if not config_path.exists():
        return DEFAULT_CONFIG.copy()

    with config_path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    return merged


def _parse_dev_guild_id(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError("dev_guild_id must be an integer or null") from None


def load_config(config_path: str = "config.yml") -> BotConfig:
    load_dotenv()
    path = Path(config_path)
    yaml_config = _load_yaml_config(path)

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required in environment or .env file")

    env_dev_guild = os.getenv("GUILD_ID") or os.getenv("DEV_GUILD_ID")
    dev_guild_id = _parse_dev_guild_id(env_dev_guild) if env_dev_guild is not None else _parse_dev_guild_id(
        yaml_config.get("dev_guild_id")
    )

    data_dir = Path(yaml_config.get("data_dir", DEFAULT_CONFIG["data_dir"]))
    data_dir.mkdir(parents=True, exist_ok=True)

    admin_ids = [
        int(role_id)
        for role_id in yaml_config.get("admin_role_ids", DEFAULT_CONFIG["admin_role_ids"])
        if str(role_id).strip() != ""
    ]

    return BotConfig(
        prefix=yaml_config.get("prefix", DEFAULT_CONFIG["prefix"]),
        admin_role_ids=admin_ids,
        log_level=yaml_config.get("log_level", DEFAULT_CONFIG["log_level"]),
        data_dir=data_dir,
        dev_guild_id=dev_guild_id,
        token=token,
    )
