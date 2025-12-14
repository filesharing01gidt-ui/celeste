from __future__ import annotations

import asyncio
import logging
import random
import re
import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.json_store import load_json, save_json

logger = logging.getLogger(__name__)


MAX_DURATION_SECONDS = 86_400
ID_CHARSET = string.ascii_uppercase + string.digits
ID_LENGTH = 5
EMBED_COLOR = 0xDCD6FF


def _parse_duration_seconds(duration: str) -> int:
    cleaned = duration.replace(" ", "").lower()
    pattern = re.compile(r"(\d+)([hms])")
    position = 0
    total_seconds = 0

    for match in pattern.finditer(cleaned):
        start, end = match.span()
        if start != position:
            raise ValueError("Invalid duration format.")
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "h":
            total_seconds += value * 3600
        elif unit == "m":
            total_seconds += value * 60
        else:
            total_seconds += value
        position = end

    if position != len(cleaned) or total_seconds == 0:
        raise ValueError("Invalid duration format.")

    return total_seconds


@dataclass
class CountdownEntry:
    id: str
    guild_id: int
    channel_id: int
    created_by_user_id: int
    created_at_ts: int
    end_ts: int
    ping_user_id: Optional[int] = None
    ping_role_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CountdownEntry":
        return cls(
            id=data["id"],
            guild_id=int(data["guild_id"]),
            channel_id=int(data["channel_id"]),
            created_by_user_id=int(data["created_by_user_id"]),
            created_at_ts=int(data["created_at_ts"]),
            end_ts=int(data["end_ts"]),
            ping_user_id=int(data["ping_user_id"]) if data.get("ping_user_id") is not None else None,
            ping_role_id=int(data["ping_role_id"]) if data.get("ping_role_id") is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "created_by_user_id": self.created_by_user_id,
            "created_at_ts": self.created_at_ts,
            "end_ts": self.end_ts,
            "ping_user_id": self.ping_user_id,
            "ping_role_id": self.ping_role_id,
        }


class Countdown(commands.Cog):
    """Slash-only countdown commands with persistence."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.countdowns_path = Path(self.bot.config.data_dir) / "countdowns.json"
        self._lock = asyncio.Lock()
        self._active: Dict[str, CountdownEntry] = {}
        self._tasks: Dict[str, asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        await self._load_existing_countdowns()

    async def cog_unload(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()

    async def _load_existing_countdowns(self) -> None:
        stored = load_json(self.countdowns_path, [])
        now = time.time()
        active_entries: Dict[str, CountdownEntry] = {}
        to_schedule: list[CountdownEntry] = []
        logger.info("Restoring %d countdown(s) from storage", len(stored))
        for raw in stored:
            try:
                entry = CountdownEntry.from_dict(raw)
            except Exception:
                logger.exception("Failed to parse countdown entry: %s", raw)
                continue

            if entry.end_ts <= now:
                await self._send_completion(entry)
                continue

            active_entries[entry.id] = entry
            to_schedule.append(entry)

        async with self._lock:
            self._active = active_entries
            self._persist()

        for entry in to_schedule:
            self._schedule_countdown(entry)

    def _persist(self) -> None:
        save_json(self.countdowns_path, [entry.to_dict() for entry in self._active.values()])

    def _schedule_countdown(self, entry: CountdownEntry) -> None:
        task = self.bot.loop.create_task(self._run_countdown(entry))
        self._tasks[entry.id] = task
        task.add_done_callback(lambda t, countdown_id=entry.id: self._tasks.pop(countdown_id, None))

    async def _run_countdown(self, entry: CountdownEntry) -> None:
        try:
            delay = max(0.0, entry.end_ts - time.time())
            await asyncio.sleep(delay)
            async with self._lock:
                if entry.id not in self._active:
                    return
            await self._send_completion(entry)
            await self._remove_entry(entry.id)
        except asyncio.CancelledError:
            logger.info("Countdown %s cancelled before completion", entry.id)
            return
        except Exception:
            logger.exception("Error running countdown %s", entry.id)

    async def _send_completion(self, entry: CountdownEntry) -> None:
        channel = self.bot.get_channel(entry.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(entry.channel_id)
            except Exception:
                logger.warning("Unable to fetch channel %s for countdown %s", entry.channel_id, entry.id)
                return

        mention = ""
        if entry.ping_user_id is not None:
            mention = f"<@{entry.ping_user_id}>"
        elif entry.ping_role_id is not None:
            mention = f"<@&{entry.ping_role_id}>"

        embed = discord.Embed(
            title=":white_check_mark: Countdown complete!",
            description=f"Ended <t:{entry.end_ts}:R>, at <t:{entry.end_ts}:t>",
            color=EMBED_COLOR,
        )
        embed.set_footer(text=f"ID: {entry.id}")

        try:
            await channel.send(content=mention, embed=embed)
        except Exception:
            logger.exception("Failed to send completion message for countdown %s", entry.id)

    async def _remove_entry(self, countdown_id: str) -> None:
        async with self._lock:
            self._active.pop(countdown_id, None)
            self._persist()

    def _generate_unique_id(self) -> str:
        existing_ids = set(self._active.keys()) | set(self._tasks.keys())
        while True:
            candidate = "".join(random.choice(ID_CHARSET) for _ in range(ID_LENGTH))
            if candidate not in existing_ids:
                return candidate

    def _build_started_embed(self, entry: CountdownEntry) -> discord.Embed:
        embed = discord.Embed(
            title=":timer: Countdown Started!",
            description=f"Ends <t:{entry.end_ts}:R>, at <t:{entry.end_ts}:t>",
            color=EMBED_COLOR,
        )
        embed.set_footer(text=f"ID: {entry.id}")
        return embed

    def _permission_embed(self, title: str, description: str, *, color: int) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        return embed

    def _has_admin_role(self, member: discord.Member) -> bool:
        expected_roles = getattr(self.bot.config, "admin_role_ids", [])
        expected_ids = {int(role_id) for role_id in expected_roles}
        return bool(expected_ids) and any(role.id in expected_ids for role in member.roles)

    @app_commands.command(name="countdown", description="Start a countdown")
    @app_commands.describe(
        duration="Duration like 1h 30m or 45s",
        ping_user="User to ping when the countdown ends",
        ping_role="Role to ping when the countdown ends",
    )
    @app_commands.guild_only()
    async def countdown(
        self,
        interaction: discord.Interaction,
        duration: str,
        ping_user: Optional[discord.Member] = None,
        ping_role: Optional[discord.Role] = None,
    ) -> None:
        if interaction.channel is None:
            await interaction.response.send_message("This command must be used in a channel.", ephemeral=True)
            return

        if ping_user and ping_role:
            embed = self._permission_embed(
                title=":no_entry: Invalid target",
                description="Please provide either a user or a role, not both.",
                color=0xE74C3C,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            duration_seconds = _parse_duration_seconds(duration)
        except ValueError:
            embed = self._permission_embed(
                title=":warning: Invalid duration",
                description="Use a format like `1h30m`, `45m`, or `30s` (max 24h).",
                color=0xE67E22,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if duration_seconds <= 0 or duration_seconds > MAX_DURATION_SECONDS:
            embed = self._permission_embed(
                title=":warning: Duration out of range",
                description="Duration must be between 1 second and 24 hours (86400 seconds).",
                color=0xE67E22,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        now_ts = int(time.time())
        entry = CountdownEntry(
            id=self._generate_unique_id(),
            guild_id=interaction.guild.id if interaction.guild else 0,
            channel_id=interaction.channel.id,
            created_by_user_id=interaction.user.id,
            created_at_ts=now_ts,
            end_ts=now_ts + duration_seconds,
            ping_user_id=ping_user.id if ping_user else None,
            ping_role_id=ping_role.id if ping_role else None,
        )

        async with self._lock:
            self._active[entry.id] = entry
            self._persist()
        self._schedule_countdown(entry)

        embed = self._build_started_embed(entry)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="cancel_countdown", description="Cancel an active countdown")
    @app_commands.describe(id="Countdown ID to cancel")
    @app_commands.guild_only()
    async def cancel_countdown(self, interaction: discord.Interaction, id: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=self._permission_embed(
                    title=":no_entry: You don’t have permission",
                    description="This command can only be used in a server by members.",
                    color=0xE74C3C,
                ),
                ephemeral=True,
            )
            return

        if not self._has_admin_role(interaction.user):
            await interaction.response.send_message(
                embed=self._permission_embed(
                    title=":no_entry: You don’t have permission",
                    description="You need the configured admin role to cancel countdowns.",
                    color=0xE74C3C,
                ),
                ephemeral=True,
            )
            return

        async with self._lock:
            entry = self._active.get(id.upper()) or self._active.get(id)
            if entry is None:
                embed = self._permission_embed(
                    title=":question: Countdown not found",
                    description="No countdown with that ID is active. Please double-check the ID.",
                    color=0xF1C40F,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            self._active.pop(entry.id, None)
            self._persist()

        task = self._tasks.pop(entry.id, None)
        if task:
            task.cancel()

        embed = self._permission_embed(
            title=":white_check_mark: Countdown cancelled successfully",
            description=f"Cancelled countdown `{entry.id}`.",
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Countdown(bot))
