from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.counter_store import COUNTER_FILENAME, increment_counter

logger = logging.getLogger(__name__)


class Core(commands.Cog):
    """Core commands demonstrating prefix, slash, and hybrid usage."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.counter_path = Path(self.bot.config.data_dir) / COUNTER_FILENAME

    @commands.hybrid_command(name="ping", description="Check the bot's latency")
    async def ping(self, ctx: commands.Context) -> None:
        latency_ms = round(self.bot.latency * 1000)
        style = "slash" if ctx.interaction else "prefix"
        message = f"Pong! `{latency_ms} ms` (invoked via {style} command)"
        await ctx.reply(message)

    @commands.hybrid_command(name="about", description="Learn about this bot")
    async def about(self, ctx: commands.Context) -> None:
        description = (
            "A discord.py 2.x example bot supporting prefix, slash, hybrid commands, and UI components."
        )
        await ctx.reply(description)

    @commands.hybrid_command(name="counter", description="Increment the per-guild counter")
    @app_commands.guild_only()
    async def counter(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.reply("This command is only available in servers.")
            return
        new_value = increment_counter(self.counter_path, ctx.guild.id)
        await ctx.reply(f"Counter incremented to `{new_value}` for this server.")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command) -> None:
        logger.debug("App command completed: %s", command.qualified_name)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Core(bot))
