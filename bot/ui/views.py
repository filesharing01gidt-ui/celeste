from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord.ext import commands

from bot.counter_store import COUNTER_FILENAME, get_counter, increment_counter

logger = logging.getLogger(__name__)


class ControlPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.counter_path = Path(self.bot.config.data_dir) / COUNTER_FILENAME

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, custom_id="panel:confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await interaction.response.send_message("Confirmed!", ephemeral=True)

    @discord.ui.button(label="🔁 Increment Counter", style=discord.ButtonStyle.primary, custom_id="panel:increment")
    async def increment(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if interaction.guild is None:
            await interaction.response.send_message("This action is only available in servers.", ephemeral=True)
            return
        new_value = increment_counter(self.counter_path, interaction.guild.id)
        content = f"Counter value: `{new_value}`"
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="🧾 Show Info", style=discord.ButtonStyle.secondary, custom_id="panel:info")
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        config = self.bot.config
        description = (
            f"Prefix: `{config.prefix}`\n"
            f"Admin roles: {', '.join(f'<@&{role_id}>' for role_id in config.admin_role_ids) if config.admin_role_ids else 'Not set'}\n"
            f"Dev guild: `{config.dev_guild_id or 'Global sync'}`"
        )
        await interaction.response.send_message(description, ephemeral=True)


def register_persistent_views(bot: commands.Bot) -> None:
    view = ControlPanelView(bot)
    bot.add_view(view)
    logger.info("Registered persistent views")
