from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import add_app_command_error_handler
from bot.config import BotConfig
from bot.ui import views as ui_views

logger = logging.getLogger(__name__)


class BotClient(commands.Bot):
    def __init__(self, config: BotConfig, intents: discord.Intents):
        super().__init__(command_prefix=config.prefix, intents=intents)
        self.config = config
        self.synced = False

    @staticmethod
    def configure_intents() -> discord.Intents:
        intents = discord.Intents.default()
        # Message content intent is required for prefix commands and must be enabled
        # in the Discord Developer Portal for your bot.
        intents.message_content = True
        intents.members = True
        return intents

    async def setup_hook(self) -> None:
        await self._load_cogs()
        ui_views.register_persistent_views(self)
        add_app_command_error_handler(self)
        await self._sync_app_commands()

    async def _load_cogs(self) -> None:
        cogs_path = Path(__file__).parent / "cogs"
        loaded: list[str] = []
        for path in cogs_path.glob("*.py"):
            if path.stem.startswith("__"):
                continue
            module = f"bot.cogs.{path.stem}"
            try:
                await self.load_extension(module)
                loaded.append(module)
            except Exception:
                logger.exception("Failed to load cog %s", module)
        logger.info("Loaded %d cogs: %s", len(loaded), ", ".join(loaded))

    async def _sync_app_commands(self) -> None:
        try:
            if self.config.dev_guild_id:
                guild = discord.Object(id=self.config.dev_guild_id)
                commands_synced = await self.tree.sync(guild=guild)
                self.synced = True
                logger.info("Synced %d app commands to guild %s", len(commands_synced), guild.id)
            else:
                commands_synced = await self.tree.sync()
                self.synced = True
                logger.info("Globally synced %d app commands; propagation may take time", len(commands_synced))
        except discord.HTTPException:
            logger.exception(
                "Failed to sync application commands. Make sure the bot was invited with the applications.commands scope."
            )
        if self.config.dev_guild_id:
            guild = discord.Object(id=self.config.dev_guild_id)
            commands_synced = await self.tree.sync(guild=guild)
            self.synced = True
            logger.info("Synced %d app commands to guild %s", len(commands_synced), guild.id)
        else:
            commands_synced = await self.tree.sync()
            self.synced = True
            logger.info("Globally synced %d app commands; propagation may take time", len(commands_synced))

    async def on_ready(self) -> None:
        if not self.synced:
            await self._sync_app_commands()
        guild_mode = f"Guild sync: {self.config.dev_guild_id}" if self.config.dev_guild_id else "Global sync"
        logger.info("Logged in as %s | %s | %d cogs", self.user, guild_mode, len(self.cogs))

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CheckFailure):
            await ctx.send(str(error))
            return
        logger.exception("Command error: %s", error)
        await ctx.send("An unexpected error occurred.")

