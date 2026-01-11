from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import prefix_admin_check

logger = logging.getLogger(__name__)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _everyone(self, guild: discord.Guild) -> discord.Role:
        return guild.default_role

    async def _set_lockdown(self, ctx: commands.Context, lock: bool) -> None:
        if ctx.guild is None or not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("This command must be used in a text channel.")
            return
        overwrite = ctx.channel.overwrites_for(self._everyone(ctx.guild))
        overwrite.send_messages = False if lock else None
        await ctx.channel.set_permissions(self._everyone(ctx.guild), overwrite=overwrite)
        state = "locked down" if lock else "unlocked"
        await ctx.send(f"Channel has been {state} for @everyone.")

    @commands.hybrid_command(name="lockdown", description="Prevent @everyone from sending messages in this channel")
    @prefix_admin_check()
    async def lockdown(self, ctx: commands.Context) -> None:
        await self._set_lockdown(ctx, True)

    @commands.hybrid_command(name="unlockdown", description="Allow @everyone to send messages again")
    @prefix_admin_check()
    async def unlockdown(self, ctx: commands.Context) -> None:
        await self._set_lockdown(ctx, False)

    @commands.hybrid_command(name="sync", description="Sync application commands")
    @prefix_admin_check()
    @app_commands.describe(guild_only="Sync only to the configured dev guild")
    async def sync(self, ctx: commands.Context, guild_only: Optional[bool] = None) -> None:
        await self._do_sync(ctx, guild_only)

    async def _do_sync(self, source: commands.Context | discord.Interaction, guild_only: Optional[bool]) -> None:
        if guild_only is None:
            guild_only = self.bot.config.dev_guild_id is not None

        if guild_only:
            if not self.bot.config.dev_guild_id:
                message = "No dev_guild_id configured; cannot sync to guild."
                await self._reply(source, message, ephemeral=True)
                return
            guild_obj = discord.Object(id=self.bot.config.dev_guild_id)
            commands_synced = await self.bot.tree.sync(guild=guild_obj)
            message = f"Synced {len(commands_synced)} commands to guild {guild_obj.id}."
        else:
            commands_synced = await self.bot.tree.sync()
            message = f"Globally synced {len(commands_synced)} commands. Global sync can take a while to propagate."
        logger.info(message)
        await self._reply(source, message, ephemeral=True)

    async def _reply(self, source: commands.Context | discord.Interaction, message: str, *, ephemeral: bool = False) -> None:
        if isinstance(source, commands.Context):
            await source.send(message)
        else:
            if source.response.is_done():
                await source.followup.send(message, ephemeral=ephemeral)
            else:
                await source.response.send_message(message, ephemeral=ephemeral)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
