from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Dict, Optional

import discord
from discord.ext import commands

from bot.campfire_store import CampfireState, load_campfire, save_campfire
from bot.economy_store import get_whitelisted_role_ids, load_economy, resolve_member_team_roles

logger = logging.getLogger(__name__)

CAMP_COLOR = 0xE67E22
BEAR_IMAGE_URLS = [
    "https://example.com/bear1.png",
    "https://example.com/bear2.png",
    "https://example.com/bear3.png",
]
BEAR_MESSAGES = [
    "# A bear is approaching!",
    "# It's coooomingggg...",
    "# You see a bear rushing towards you...",
    "# A hungry bear runs in your direction...",
    "# It seems like you have a visitor...",
    "# Oh no!!!",
    "# Say HI!!!",
    "# You are not alone in this forest...",
    "# I see you...",
]
MERCY_SECONDS = 20 * 60
MIN_BEAR_DELAY = 30
MAX_BEAR_DELAY = 240


class Campfire(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.campfire_path = Path(self.bot.config.data_dir) / "campfire.json"
        self.economy_path = Path(self.bot.config.data_dir) / "economy.json"
        self._lock = asyncio.Lock()
        self._states: Dict[int, CampfireState] = load_campfire(self.campfire_path)
        self._tasks: Dict[int, asyncio.Task[None]] = {}
        self._add_debounce: Dict[int, float] = {}

    def _error_embed(self, title: str, description: str, *, color: int = 0xE74C3C) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color)

    def _camp_embed(self, team_role: discord.Role, fuel: int) -> discord.Embed:
        embed = discord.Embed(
            title=f"{team_role.name}'s Campfire",
            description=f"**{fuel} Fuel Points** 🔥",
            color=CAMP_COLOR,
        )
        return embed

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

    def _get_state(self, team_role_id: int) -> CampfireState:
        state = self._states.get(team_role_id)
        if state is None:
            state = CampfireState()
            self._states[team_role_id] = state
        return state

    def _persist(self) -> None:
        save_campfire(self.campfire_path, self._states)

    def _schedule_bear_task(self, team_role: discord.Role, channel: discord.TextChannel, state: CampfireState) -> None:
        task = self.bot.loop.create_task(self._bear_loop(team_role, channel, state))
        self._tasks[team_role.id] = task
        task.add_done_callback(lambda t, role_id=team_role.id: self._tasks.pop(role_id, None))

    async def _bear_loop(self, team_role: discord.Role, channel: discord.TextChannel, state: CampfireState) -> None:
        assert state.started_at is not None
        cutoff = state.started_at + MERCY_SECONDS
        while True:
            now = time.time()
            if now >= cutoff:
                break
            delay = random.randint(MIN_BEAR_DELAY, MAX_BEAR_DELAY)
            await asyncio.sleep(delay)
            if time.time() >= cutoff:
                break
            await self._trigger_bear_event(team_role, channel)

    async def _trigger_bear_event(self, team_role: discord.Role, channel: discord.TextChannel) -> None:
        message_text = random.choice(BEAR_MESSAGES)
        image_url = random.choice(BEAR_IMAGE_URLS)
        try:
            await channel.send(content=message_text, embed=discord.Embed(color=CAMP_COLOR).set_image(url=image_url))
        except Exception:
            logger.exception("Failed to send bear event message")
            return

        def check(msg: discord.Message) -> bool:
            return (
                msg.channel.id == channel.id
                and isinstance(msg.author, discord.Member)
                and any(role.id == team_role.id for role in msg.author.roles)
                and msg.content.count("A") >= 11
            )

        try:
            await self.bot.wait_for("message", timeout=5, check=check)
            try:
                await channel.send(embed=discord.Embed(title="The bear retreats!", color=CAMP_COLOR))
            except Exception:
                logger.debug("Failed to send retreat embed")
        except asyncio.TimeoutError:
            await self._apply_bear_damage(team_role, channel)

    async def _apply_bear_damage(self, team_role: discord.Role, channel: discord.TextChannel) -> None:
        async with self._lock:
            state = self._get_state(team_role.id)
            damage = random.randint(3, 5)
            state.fuel_points = max(0, state.fuel_points - damage)
            self._persist()
            fuel = state.fuel_points

        embed = discord.Embed(
            title=f"{team_role.name}'s Campfire",
            description=f"Bear attack! Lost **{damage}** fuel.\n**{fuel} Fuel Points** 🔥",
            color=CAMP_COLOR,
        )
        try:
            await channel.send(embed=embed)
        except Exception:
            logger.debug("Failed to send bear damage embed")

    def _add_firewood(self, team_role_id: int) -> int:
        state = self._get_state(team_role_id)
        state.fuel_points += 3
        self._persist()
        return state.fuel_points

    def _stop_camping_state(self, team_role_id: int) -> None:
        state = self._get_state(team_role_id)
        state.is_camping = False
        state.channel_id = None
        state.started_at = None
        self._persist()

    @commands.command(name="campfire")
    async def campfire(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member):
            return
        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, error_embed = self._resolve_member_team_role(ctx.author, whitelisted_ids)
        if team_role is None:
            await ctx.send(embed=error_embed)
            return
        async with self._lock:
            fuel = self._get_state(team_role.id).fuel_points
        await ctx.send(embed=self._camp_embed(team_role, fuel))

    @commands.command(name="add-firewood")
    async def add_firewood(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member):
            return
        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, error_embed = self._resolve_member_team_role(ctx.author, whitelisted_ids)
        if team_role is None:
            await ctx.send(embed=error_embed)
            return

        now = time.monotonic()
        last = self._add_debounce.get(team_role.id, 0)
        if now - last < 1:
            return
        self._add_debounce[team_role.id] = now

        async with self._lock:
            total = self._add_firewood(team_role.id)

        embed = self._camp_embed(team_role, total)
        await ctx.send(embed=embed)
        if total >= 100:
            await ctx.send("Congratulations! Your campfire is roaring. Run `!stop-camping` to wrap up.")

    @commands.command(name="start-camping")
    async def start_camping(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member):
            return
        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, error_embed = self._resolve_member_team_role(ctx.author, whitelisted_ids)
        if team_role is None:
            await ctx.send(embed=error_embed)
            return
        async with self._lock:
            state = self._get_state(team_role.id)
            if state.is_camping:
                await ctx.send(embed=self._error_embed("Already camping!", "Your campfire is already active.", color=CAMP_COLOR))
                return
            state.is_camping = True
            state.channel_id = ctx.channel.id
            state.started_at = time.time()
            self._persist()
        await ctx.send(embed=self._camp_embed(team_role, self._get_state(team_role.id).fuel_points))
        self._schedule_bear_task(team_role, ctx.channel, self._get_state(team_role.id))

    @commands.command(name="stop-camping")
    async def stop_camping(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member):
            return
        whitelisted_ids = self._load_whitelisted_role_ids()
        team_role, error_embed = self._resolve_member_team_role(ctx.author, whitelisted_ids)
        if team_role is None:
            await ctx.send(embed=error_embed)
            return
        async with self._lock:
            state = self._get_state(team_role.id)
            if not state.is_camping:
                await ctx.send(embed=self._error_embed("Not currently camping.", "Start with !start-camping first.", color=CAMP_COLOR))
                return
            task = self._tasks.pop(team_role.id, None)
            if task:
                task.cancel()
            self._stop_camping_state(team_role.id)
        await ctx.send(embed=self._error_embed("Stopped camping!", "Your campfire is resting.", color=CAMP_COLOR))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Campfire(bot))
