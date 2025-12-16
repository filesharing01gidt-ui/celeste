from __future__ import annotations

import asyncio
import logging
import random
import re
import string
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.economy_store import get_whitelisted_role_ids, load_economy, resolve_member_team_roles
from bot.json_store import load_json, save_json

logger = logging.getLogger(__name__)


MAX_DURATION_SECONDS = 86_400
ID_CHARSET = string.ascii_uppercase + string.digits
ID_LENGTH = 5
EMBED_COLOR = 0xDCD6FF


def _seconds_until_next_interval_boundary(now: datetime, interval_minutes: int) -> int:
    """Return seconds until the next interval boundary from the provided aware datetime.

    Example:
        >>> _seconds_until_next_interval_boundary(datetime(2024, 1, 1, 5, 27, tzinfo=timezone.utc), 15)
        180
    """

    if interval_minutes <= 0:
        return 0

    interval_seconds = interval_minutes * 60
    seconds_today = now.hour * 3600 + now.minute * 60 + now.second
    remainder = seconds_today % interval_seconds
    if remainder == 0:
        return 0
    return interval_seconds - remainder


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
    start_ts: Optional[int] = None
    end_ts: int
    ping_user_id: Optional[int] = None
    ping_role_id: Optional[int] = None
    kind: str = "countdown"
    team_role_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CountdownEntry":
        return cls(
            id=str(data["id"]).upper(),
            guild_id=int(data["guild_id"]),
            channel_id=int(data["channel_id"]),
            created_by_user_id=int(data["created_by_user_id"]),
            created_at_ts=int(data["created_at_ts"]),
            start_ts=int(data["start_ts"]) if data.get("start_ts") is not None else None,
            end_ts=int(data["end_ts"]),
            ping_user_id=int(data["ping_user_id"]) if data.get("ping_user_id") is not None else None,
            ping_role_id=int(data["ping_role_id"]) if data.get("ping_role_id") is not None else None,
            kind=data.get("kind", "countdown"),
            team_role_id=int(data["team_role_id"]) if data.get("team_role_id") is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "created_by_user_id": self.created_by_user_id,
            "created_at_ts": self.created_at_ts,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "ping_user_id": self.ping_user_id,
            "ping_role_id": self.ping_role_id,
            "kind": self.kind,
            "team_role_id": self.team_role_id,
        }


class Countdown(commands.Cog):
    """Slash-only countdown commands with persistence."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.countdowns_path = Path(self.bot.config.data_dir) / "countdowns.json"
        self.economy_path = Path(self.bot.config.data_dir) / "economy.json"
        self._lock = asyncio.Lock()
        self._active: Dict[str, CountdownEntry] = {}
        self._tasks: Dict[str, asyncio.Task[None]] = {}
        self._wait_tasks: Dict[str, asyncio.Task[None]] = {}
        self._travel_debounce: Dict[tuple[int, int], float] = {}
        self._interval_groups: Dict[tuple[int, int, int], Dict[str, object]] = {}

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

            if entry.kind == "travel" and entry.team_role_id is None and entry.ping_role_id is not None:
                entry.team_role_id = entry.ping_role_id

            if entry.start_ts is None:
                entry.start_ts = entry.created_at_ts

            if entry.end_ts <= now:
                await self._send_completion(entry)
                continue

            active_entries[entry.id] = entry
            to_schedule.append(entry)

            if entry.kind == "travel" and entry.start_ts > now and entry.team_role_id is not None:
                key = (entry.guild_id, entry.start_ts, entry.end_ts)
                group = self._interval_groups.setdefault(
                    key,
                    {
                        "team_role_ids": set(),
                        "entry_ids": set(),
                        "thread_id": None,
                        "member_ids": set(),
                        "created_at": time.time(),
                    },
                )
                group["team_role_ids"].add(entry.team_role_id)
                group["entry_ids"].add(entry.id)

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

    def _cancel_wait_task(self, countdown_id: str) -> None:
        wait_task = self._wait_tasks.pop(countdown_id, None)
        if wait_task:
            wait_task.cancel()

    async def _send_travel_start_after_wait(self, interaction: discord.Interaction, entry: CountdownEntry, wait_seconds: int, mention: str) -> None:
        try:
            await asyncio.sleep(wait_seconds)
            if entry.id not in self._active:
                return
            await self._ensure_interval_thread(entry)
            embed_color = self._travel_color(entry.guild_id, entry.team_role_id)
            embed = self._build_started_embed(entry, color=embed_color)
            await interaction.followup.send(embed=embed, content=mention)
        except Exception:
            logger.exception("Failed to send travel start notification for %s", entry.id)
        finally:
            self._wait_tasks.pop(entry.id, None)

    async def _run_countdown(self, entry: CountdownEntry) -> None:
        try:
            delay = max(0.0, entry.end_ts - time.time())
            await asyncio.sleep(delay)
            async with self._lock:
                if entry.id not in self._active:
                    return
            await self._send_completion(entry)
            await self._cleanup_interval_group(entry, completed=True)
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

        embed_color = EMBED_COLOR
        if entry.kind == "travel":
            embed_color = self._travel_color(entry.guild_id, entry.team_role_id or entry.ping_role_id)

        embed = discord.Embed(
            title=":white_check_mark: Countdown complete!",
            description=f"Ended <t:{entry.end_ts}:R>, at <t:{entry.end_ts}:t>",
            color=embed_color,
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

    def _register_interval_group(self, entry: CountdownEntry) -> None:
        if entry.start_ts is None or entry.team_role_id is None:
            return
        key = (entry.guild_id, entry.start_ts, entry.end_ts)
        group = self._interval_groups.setdefault(
            key,
            {
                "team_role_ids": set(),
                "entry_ids": set(),
                "thread_id": None,
                "member_ids": set(),
                "created_at": time.time(),
            },
        )
        group["team_role_ids"].add(entry.team_role_id)
        group["entry_ids"].add(entry.id)

    async def _ensure_interval_thread(self, entry: CountdownEntry) -> Optional[int]:
        if entry.start_ts is None:
            return None
        key = (entry.guild_id, entry.start_ts, entry.end_ts)
        async with self._lock:
            group = self._interval_groups.get(key)
            if not group:
                return None
            if group.get("thread_id") not in (None, "pending"):
                return group["thread_id"]  # type: ignore[return-value]
            team_role_ids: set[int] = group["team_role_ids"]  # type: ignore[assignment]
            if len(team_role_ids) < 2:
                return None
            if group.get("thread_id") == "pending":
                return None
            group["thread_id"] = "pending"

        guild = self.bot.get_guild(entry.guild_id)
        if guild is None:
            return None

        channel = self.bot.get_channel(1450520738485506059)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(1450520738485506059)
            except Exception:
                logger.exception("Failed to fetch transit thread channel")
                return None

        start_dt = datetime.fromtimestamp(entry.start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(entry.end_ts, tz=timezone.utc)
        name = f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')} Transit"
        try:
            thread = await channel.create_thread(
                name=name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason="Shared transit interval grouping",
            )
        except Exception:
            logger.exception("Failed to create transit thread")
            async with self._lock:
                if key in self._interval_groups:
                    self._interval_groups[key]["thread_id"] = None
            return None

        async with self._lock:
            group = self._interval_groups.get(key)
            if not group:
                return thread.id
            group["thread_id"] = thread.id
            team_role_ids = group["team_role_ids"]  # type: ignore[assignment]
            member_ids = group["member_ids"]  # type: ignore[assignment]

        mentions = []
        for role_id in team_role_ids:
            role = guild.get_role(role_id)
            if role:
                mentions.append(role.mention)
                for member in role.members:
                    try:
                        await thread.add_user(member)
                        member_ids.add(member.id)
                    except Exception:
                        logger.warning("Failed adding member %s to transit thread", member.id)

        body = "You are alone on this travel." if len(team_role_ids) == 1 else "\n".join(mentions)
        aboard_embed = discord.Embed(
            title=":question_mark: Who is aboard?",
            description=body,
            color=EMBED_COLOR,
        )
        try:
            await thread.send(content=" ".join(mentions) if mentions else None, embed=aboard_embed)
        except Exception:
            logger.exception("Failed to send aboard embed to transit thread")

        return thread.id

    def _build_started_embed(
        self, entry: CountdownEntry, *, color: discord.Color | int | None = None
    ) -> discord.Embed:
        embed_color = color if color is not None else EMBED_COLOR
        embed = discord.Embed(
            title=":timer: Countdown Started!",
            description=f"Ends <t:{entry.end_ts}:R>, at <t:{entry.end_ts}:t>",
            color=embed_color,
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

    def _load_whitelisted_role_ids(self) -> set[int]:
        data = load_economy(self.economy_path)
        return get_whitelisted_role_ids(data)

    def _resolve_member_team_role(
        self, member: discord.Member, whitelisted_ids: set[int]
    ) -> tuple[discord.Role | None, discord.Embed | None]:
        matches = resolve_member_team_roles(member.roles, whitelisted_ids)
        if len(matches) == 1:
            return matches[0], None
        if len(matches) == 0:
            embed = self._permission_embed(
                title=":no_entry: No team role found",
                description="You don't have any whitelisted team roles. Please contact an admin to be added.",
                color=0xE74C3C,
            )
            return None, embed
        embed = self._permission_embed(
            title=":warning: Multiple team roles detected",
            description="You have multiple whitelisted team roles: "
            + ", ".join(role.mention for role in matches)
            + ". Please keep only one.",
            color=0xF1C40F,
        )
        return None, embed

    def _travel_color(self, guild_id: int, role_id: Optional[int]) -> discord.Color | int:
        if role_id is not None:
            guild = self.bot.get_guild(guild_id)
            if guild:
                role = guild.get_role(role_id)
                if role:
                    return role.color
        return EMBED_COLOR

    async def _cleanup_interval_group(self, entry: CountdownEntry, *, completed: bool) -> None:
        if entry.start_ts is None or entry.kind != "travel":
            return
        key = (entry.guild_id, entry.start_ts, entry.end_ts)
        group = self._interval_groups.get(key)
        if not group:
            return
        entry_ids: set[str] = group.get("entry_ids", set())  # type: ignore[assignment]
        entry_ids.discard(entry.id)
        team_role_ids: set[int] = group.get("team_role_ids", set())  # type: ignore[assignment]
        member_ids: set[int] = group.get("member_ids", set())  # type: ignore[assignment]

        if not entry_ids:
            thread_id = group.get("thread_id")
            if thread_id is not None:
                thread = self.bot.get_channel(thread_id)
                if isinstance(thread, discord.Thread):
                    try:
                        await thread.edit(archived=True, locked=True)
                    except Exception:
                        logger.warning("Failed to archive transit thread %s", thread_id)
                    for member_id in list(member_ids):
                        member = thread.guild.get_member(member_id) if thread.guild else None
                        if member:
                            try:
                                await thread.remove_user(member)
                            except Exception:
                                logger.warning("Failed to remove member %s from transit thread", member_id)
            self._interval_groups.pop(key, None)

    def _find_recent_travel_for_team(self, team_role_id: int) -> Optional[CountdownEntry]:
        candidates = [
            entry
            for entry in self._active.values()
            if entry.kind == "travel" and entry.team_role_id == team_role_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.created_at_ts)

    async def _cancel_recent_travel(self, interaction: discord.Interaction) -> None:
        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, error_embed = self._resolve_member_team_role(interaction.user, whitelisted_ids)
        if team_role is None and error_embed is not None:
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        assert team_role is not None
        entry = self._find_recent_travel_for_team(team_role.id)
        if entry is None:
            embed = self._permission_embed(
                title=":question: Countdown not found",
                description="No active travel countdown found for your team role.",
                color=0xF1C40F,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if time.time() - entry.created_at_ts > 180:
            embed = self._permission_embed(
                title=":no_entry: Cannot cancel",
                description="Travel cannot be changed after 3 minutes have passed.",
                color=0xE74C3C,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self._cancel_entry(entry)

        embed = self._permission_embed(
            title=":white_check_mark: Countdown cancelled successfully",
            description=f"Cancelled countdown `{entry.id}` for {team_role.mention}.",
            color=team_role.color,
        )
        await interaction.response.send_message(embed=embed)

    async def _cancel_entry(self, entry: CountdownEntry) -> None:
        async with self._lock:
            self._active.pop(entry.id, None)
            self._persist()

        self._cancel_wait_task(entry.id)
        task = self._tasks.pop(entry.id, None)
        if task:
            task.cancel()
        await self._cleanup_interval_group(entry, completed=False)

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
            start_ts=now_ts,
            end_ts=now_ts + duration_seconds,
            ping_user_id=ping_user.id if ping_user else None,
            ping_role_id=ping_role.id if ping_role else None,
            kind="countdown",
        )

        async with self._lock:
            self._active[entry.id] = entry
            self._persist()
        self._schedule_countdown(entry)

        embed = self._build_started_embed(entry)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="travel", description="Start a travel countdown with optional interval")
    @app_commands.describe(duration="Duration like 1h 30m or 45s", interval="Interval minutes (0-30) to align start")
    @app_commands.guild_only()
    async def travel(self, interaction: discord.Interaction, duration: str, interval: int | None = 0) -> None:
        if interaction.channel is None or interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=self._permission_embed(
                    title=":no_entry: Guild only",
                    description="This command must be used in a server channel.",
                    color=0xE74C3C,
                ),
                ephemeral=True,
            )
            return

        interval_value = int(interval or 0)
        if interval_value < 0 or interval_value > 30:
            embed = self._permission_embed(
                title=":warning: Invalid interval",
                description="Interval must be between 0 and 30 minutes.",
                color=0xE67E22,
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

        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, error_embed = self._resolve_member_team_role(interaction.user, whitelisted_ids)
        if team_role is None and error_embed is not None:
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        now = datetime.now(timezone.utc)
        wait_seconds = _seconds_until_next_interval_boundary(now, interval_value)
        start_ts = int(now.timestamp()) + wait_seconds
        entry = CountdownEntry(
            id=self._generate_unique_id(),
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            created_by_user_id=interaction.user.id,
            created_at_ts=int(time.time()),
            start_ts=start_ts,
            end_ts=start_ts + duration_seconds,
            ping_role_id=team_role.id,
            kind="travel",
            team_role_id=team_role.id,
        )

        debounce_embed: discord.Embed | None = None
        now_monotonic = time.monotonic()
        async with self._lock:
            key = (interaction.guild.id, team_role.id)
            last = self._travel_debounce.get(key)
            if last is not None and now_monotonic - last < 5:
                debounce_embed = self._permission_embed(
                    title=":warning: Slow down",
                    description="You just did one — slow down.",
                    color=0xF1C40F,
                )
            else:
                self._travel_debounce[key] = now_monotonic
                self._active[entry.id] = entry
                self._persist()
                if wait_seconds > 0:
                    self._register_interval_group(entry)

        if debounce_embed is not None:
            await interaction.response.send_message(embed=debounce_embed, ephemeral=True)
            return

        self._schedule_countdown(entry)

        mention = f"<@&{team_role.id}>"
        if wait_seconds > 0:
            embed_color = self._travel_color(interaction.guild.id, team_role.id)
            wait_embed = discord.Embed(
                title="🚏 You are waiting for your ride…",
                description=f"Countdown will start <t:{start_ts}:R>, at <t:{start_ts}:t>",
                color=embed_color,
            )
            wait_embed.set_footer(text=f"ID: {entry.id}")
            await interaction.response.send_message(embed=wait_embed)
            wait_task = self.bot.loop.create_task(
                self._send_travel_start_after_wait(interaction, entry, wait_seconds, mention)
            )
            self._wait_tasks[entry.id] = wait_task
            wait_task.add_done_callback(lambda t, countdown_id=entry.id: self._wait_tasks.pop(countdown_id, None))
        else:
            embed_color = self._travel_color(interaction.guild.id, team_role.id)
            embed = self._build_started_embed(entry, color=embed_color)
            await interaction.response.send_message(embed=embed, content=mention)

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

        normalized = id.strip()
        if normalized.lower() == "recent":
            await self._cancel_recent_travel(interaction)
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

        entry = self._active.get(normalized.upper())
        if entry is None:
            embed = self._permission_embed(
                title=":question: Countdown not found",
                description="No countdown with that ID is active. Please double-check the ID.",
                color=0xF1C40F,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self._cancel_entry(entry)

        embed = self._permission_embed(
            title=":white_check_mark: Countdown cancelled successfully",
            description=f"Cancelled countdown `{entry.id}`.",
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Countdown(bot))
