from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.counter_store import COUNTER_FILENAME, get_counter
from bot.ui.views import ControlPanelView

logger = logging.getLogger(__name__)


class UIDemo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.counter_path = Path(self.bot.config.data_dir) / COUNTER_FILENAME

    @app_commands.command(name="panel", description="Display an interactive control panel")
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command is only available in servers.", ephemeral=True)
            return
        counter_value = get_counter(self.counter_path, interaction.guild.id)
        view = ControlPanelView(self.bot)
        content = f"Counter value: `{counter_value}`"
        await interaction.response.send_message(content, view=view)
        logger.info("Panel sent by %s in %s", interaction.user, interaction.channel)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UIDemo(bot))
