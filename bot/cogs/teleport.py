from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from bot.economy_store import get_whitelisted_role_ids, load_economy, resolve_member_team_roles
from bot.teleport_store import TeleportPing, TeleportTrigger, load_teleport, save_teleport

logger = logging.getLogger(__name__)


READ_ONLY_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True,
    read_message_history=True,
    send_messages=False,
    add_reactions=None,
    create_public_threads=False,
    create_private_threads=False,
    send_messages_in_threads=False,
)


class Teleport(commands.Cog):
    teleport = app_commands.Group(name="teleport", description="Manage teleport triggers")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.teleport_path = Path(self.bot.config.data_dir) / "teleport.json"
        self.economy_path = Path(self.bot.config.data_dir) / "economy.json"
        self._lock = asyncio.Lock()
        self._debounce: Dict[Tuple[int, int, str], float] = {}
        self._active_locks: Dict[Tuple[int, int], asyncio.Lock] = {}

    # -----------------
    # Helpers
    # -----------------
    def _error_embed(self, title: str, description: str, *, color: int = 0xE74C3C) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color)

    def _success_embed(self, title: str, description: str, *, color: int | discord.Color = 0x2ECC71) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color)

    def _has_admin_role(self, member: discord.Member) -> bool:
        expected_roles = getattr(self.bot.config, "admin_role_ids", [])
        expected_ids = {int(role_id) for role_id in expected_roles}
        return bool(expected_ids) and any(role.id in expected_ids for role in member.roles)

    async def _require_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            embed = self._error_embed(
                title=":no_entry: Guild only",
                description="This command can only be used inside a server.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        if not self._has_admin_role(interaction.user):
            embed = self._error_embed(
                title=":no_entry: You don't have permission",
                description="You need an admin role to do that.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    def _resolve_member_team_role(
        self, member: discord.Member, whitelisted_ids: set[int]
    ) -> tuple[discord.Role | None, discord.Embed | None]:
        matches = resolve_member_team_roles(member.roles, whitelisted_ids)
        if len(matches) == 1:
            return matches[0], None
        if len(matches) == 0:
            embed = self._error_embed(
                title=":no_entry: No team role found",
                description="You don't have any whitelisted team roles. Please contact an admin to be added.",
            )
            return None, embed
        embed = self._error_embed(
            title=":warning: Multiple team roles detected",
            description="You have multiple whitelisted team roles: "
            + ", ".join(role.mention for role in matches)
            + ". Please keep only one.",
            color=0xF1C40F,
        )
        return None, embed

    def _load_whitelisted_role_ids(self) -> set[int]:
        data = load_economy(self.economy_path)
        return get_whitelisted_role_ids(data)

    def _load_data(self) -> dict:
        return load_teleport(self.teleport_path)

    def _save_data(self, data: dict) -> None:
        save_teleport(self.teleport_path, data)

    def _validate_trigger(self, trigger: str) -> tuple[str | None, discord.Embed | None]:
        normalized = trigger.strip().lower()
        if normalized.startswith("?"):
            normalized = normalized[1:]
        if not normalized:
            return None, self._error_embed(
                title=":warning: Invalid trigger",
                description="Trigger cannot be empty.",
            )
        if not re.fullmatch(r"[a-z0-9-_]+", normalized):
            return None, self._error_embed(
                title=":warning: Invalid trigger",
                description="Use only letters, numbers, hyphens, or underscores.",
            )
        return normalized, None

    def _get_team_lock(self, guild_id: int, team_role_id: int) -> asyncio.Lock:
        key = (guild_id, team_role_id)
        if key not in self._active_locks:
            self._active_locks[key] = asyncio.Lock()
        return self._active_locks[key]

    def _team_role_from_id(self, guild: discord.Guild, team_role_id: int) -> Optional[discord.Role]:
        return guild.get_role(team_role_id)

    def _most_recent_ping(self, pings: List[TeleportPing], guild_id: int, team_role_id: int) -> Optional[TeleportPing]:
        matches = [ping for ping in pings if ping.guild_id == guild_id and ping.team_role_id == team_role_id]
        if not matches:
            return None
        return max(matches, key=lambda p: p.timestamp)

    async def _delete_ping_records(
        self,
        guild: discord.Guild,
        pings: List[TeleportPing],
        *,
        keep_channel_ids: set[int],
        team_role_id: int,
    ) -> List[TeleportPing]:
        remaining: List[TeleportPing] = []
        for ping in pings:
            if ping.guild_id != guild.id or ping.team_role_id != team_role_id:
                remaining.append(ping)
                continue
            if keep_channel_ids and ping.channel_id in keep_channel_ids:
                remaining.append(ping)
                continue

            channel = guild.get_channel(ping.channel_id)
            role = guild.get_role(ping.team_role_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)) and role:
                try:
                    await channel.set_permissions(role, overwrite=None)
                except Exception:
                    logger.warning("Failed clearing permissions for role %s in %s", ping.team_role_id, ping.channel_id)
                if isinstance(channel, (discord.TextChannel, discord.Thread)):
                    try:
                        message = await channel.fetch_message(ping.message_id)
                        await message.delete()
                    except Exception:
                        logger.debug("Ping message %s already gone in channel %s", ping.message_id, ping.channel_id)
            # do not keep this ping
        return remaining

    async def _grant_channel_access(
        self, channel: discord.TextChannel, role: discord.Role
    ) -> None:
        try:
            await channel.set_permissions(role, overwrite=READ_ONLY_OVERWRITE)
        except Exception:
            logger.exception("Failed to set permissions for %s in %s", role.id, channel.id)

    async def _send_ping(
        self, channel: discord.TextChannel, role: discord.Role, trigger: str
    ) -> Optional[int]:
        try:
            message = await channel.send(content=role.mention)
            return message.id
        except Exception:
            logger.exception("Failed to send ping in %s for trigger %s", channel.id, trigger)
            return None

    # -----------------
    # Slash commands
    # -----------------
    @teleport.command(name="add", description="Add a teleport trigger")
    @app_commands.describe(
        trigger="Name of the trigger (without ? prefix)",
        channel="Channel to teleport to",
        parent="Optional parent channel to keep access to",
    )
    @app_commands.guild_only()
    async def teleport_add(
        self,
        interaction: discord.Interaction,
        trigger: str,
        channel: discord.TextChannel,
        parent: Optional[discord.TextChannel] = None,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        normalized, error_embed = self._validate_trigger(trigger)
        if error_embed or normalized is None:
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        async with self._lock:
            data = self._load_data()
            triggers: Dict[str, TeleportTrigger] = data.get("triggers", {})
            if normalized in triggers:
                embed = self._error_embed(
                    title=":warning: Trigger exists",
                    description="Trigger already exists, use remove first.",
                    color=0xF1C40F,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            triggers[normalized] = TeleportTrigger(
                trigger=normalized,
                target_channel_id=channel.id,
                parent_channel_id=parent.id if parent else None,
            )
            data["triggers"] = triggers
            self._save_data(data)

        embed = self._success_embed(
            title=":white_check_mark: Teleport added",
            description=f"`?{normalized}` will send teams to {channel.mention}.",
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @teleport.command(name="remove", description="Remove a teleport trigger")
    @app_commands.describe(trigger="Name of the trigger to remove")
    @app_commands.guild_only()
    async def teleport_remove(self, interaction: discord.Interaction, trigger: str) -> None:
        if not await self._require_admin(interaction):
            return

        normalized, error_embed = self._validate_trigger(trigger)
        if error_embed or normalized is None:
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        async with self._lock:
            data = self._load_data()
            triggers: Dict[str, TeleportTrigger] = data.get("triggers", {})
            if normalized not in triggers:
                embed = self._error_embed(
                    title=":question: Trigger not found",
                    description=f"`?{normalized}` is not configured.",
                    color=0xF1C40F,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            triggers.pop(normalized, None)
            data["triggers"] = triggers
            self._save_data(data)

        embed = self._success_embed(
            title=":white_check_mark: Teleport removed",
            description=f"Removed trigger `?{normalized}`.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------
    # Message handling
    # -----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content.startswith("?"):
            return

        content = message.content
        if content.startswith("?routeinfo"):
            await self._handle_routeinfo(message)
            return

        parts = content[1:].split()
        if not parts:
            return
        key = parts[0].strip().lower()

        async with self._lock:
            data = self._load_data()
        triggers: Dict[str, TeleportTrigger] = data.get("triggers", {})
        if key not in triggers:
            return

        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, error_embed = self._resolve_member_team_role(message.author, whitelisted_ids)
        if team_role is None or error_embed is not None:
            await message.channel.send(embed=error_embed)
            return

        now = time.monotonic()
        debounce_key = (message.guild.id, team_role.id, key)
        last_used = self._debounce.get(debounce_key, 0)
        if now - last_used < 2:
            embed = self._error_embed(
                title=":warning: Slow down.",
                description="Please wait a moment before triggering this teleport again.",
                color=0xF1C40F,
            )
            await message.channel.send(embed=embed)
            return

        lock = self._get_team_lock(message.guild.id, team_role.id)
        async with lock:
            await self._handle_teleport(message, team_role, triggers[key])
            self._debounce[debounce_key] = time.monotonic()

    async def _handle_routeinfo(self, message: discord.Message) -> None:
        if message.guild is None or not isinstance(message.author, discord.Member):
            return

        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, _ = self._resolve_member_team_role(message.author, whitelisted_ids)
        if team_role is None:
            return

        async with self._lock:
            data = self._load_data()
            pings: List[TeleportPing] = data.get("pings", [])
            remaining = [ping for ping in pings if not (ping.guild_id == message.guild.id and ping.team_role_id == team_role.id)]
            removal_targets = [ping for ping in pings if ping.guild_id == message.guild.id and ping.team_role_id == team_role.id]
            data["pings"] = remaining
            self._save_data(data)

        for ping in removal_targets:
            channel = message.guild.get_channel(ping.channel_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
                try:
                    await channel.set_permissions(team_role, overwrite=None)
                except Exception:
                    logger.debug("Failed to clear permissions during routeinfo cleanup for %s", team_role.id)
                if isinstance(channel, (discord.TextChannel, discord.Thread)):
                    try:
                        msg = await channel.fetch_message(ping.message_id)
                        await msg.delete()
                    except Exception:
                        logger.debug("Ping message already removed during routeinfo cleanup")

    async def _handle_teleport(
        self, message: discord.Message, team_role: discord.Role, trigger: TeleportTrigger
    ) -> None:
        guild = message.guild
        if guild is None:
            return

        async with self._lock:
            data = self._load_data()
            pings: List[TeleportPing] = data.get("pings", [])

        most_recent = self._most_recent_ping(pings, guild.id, team_role.id)
        target_channel = guild.get_channel(trigger.target_channel_id)
        parent_channel_id = trigger.parent_channel_id
        if not isinstance(target_channel, discord.TextChannel):
            embed = self._error_embed(
                title=":question: Channel missing",
                description="The target channel for this teleport no longer exists.",
                color=0xF1C40F,
            )
            await message.channel.send(embed=embed)
            return

        if most_recent and most_recent.channel_id == target_channel.id:
            perms = target_channel.permissions_for(team_role)
            if perms.view_channel:
                embed = self._error_embed(
                    title=":information_source: You are already there.",
                    description=f"You already have access to {target_channel.mention}.",
                    color=0x3498DB,
                )
                await message.channel.send(embed=embed)
                return

        keep_channels: set[int] = set()
        if parent_channel_id:
            keep_channels.add(parent_channel_id)

        async with self._lock:
            data = self._load_data()
            current_pings: List[TeleportPing] = data.get("pings", [])
            cleaned = await self._delete_ping_records(
                guild, current_pings, keep_channel_ids=keep_channels, team_role_id=team_role.id
            )
            data["pings"] = cleaned
            self._save_data(data)

        if parent_channel_id:
            parent_channel = guild.get_channel(parent_channel_id)
            if isinstance(parent_channel, discord.TextChannel):
                await self._grant_channel_access(parent_channel, team_role)

        await self._grant_channel_access(target_channel, team_role)
        ping_message_id = await self._send_ping(target_channel, team_role, trigger.trigger)

        if ping_message_id is not None:
            new_ping = TeleportPing(
                guild_id=guild.id,
                team_role_id=team_role.id,
                channel_id=target_channel.id,
                message_id=ping_message_id,
                trigger=trigger.trigger,
                timestamp=time.time(),
            )
            async with self._lock:
                data = self._load_data()
                pings: List[TeleportPing] = data.get("pings", [])
                pings.append(new_ping)
                data["pings"] = pings
                self._save_data(data)

        await message.channel.send(
            embed=self._success_embed(
                title="✨ Moved!",
                description=f"Moved to {target_channel.mention}.",
                color=0xDCD6FF,
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Teleport(bot))
