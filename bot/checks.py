from __future__ import annotations

import logging
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


def _has_role(member: discord.Member, role_ids: Iterable[int]) -> bool:
    expected_ids = {int(role_id) for role_id in role_ids}
    return any(role.id in expected_ids for role in member.roles)


def prefix_admin_check(role_ids: Iterable[int] | None = None) -> commands.Check:
    def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.CheckFailure("This command can only be used in a server.")
        assert isinstance(ctx.author, discord.Member)
        expected_role_ids = set(role_ids or getattr(getattr(ctx.bot, "config", None), "admin_role_ids", []))
        if not expected_role_ids:
            raise commands.CheckFailure("Admin role not configured.")
        if not _has_role(ctx.author, expected_role_ids):
            mentions = ", ".join(f"<@&{role_id}>" for role_id in expected_role_ids)
            raise commands.CheckFailure(f"You need one of these roles to run this command: {mentions}.")
        return True

    return commands.check(predicate)


def app_command_admin_check(role_ids: Iterable[int] | None = None) -> app_commands.Check:
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("This command can only be used in a server.")

        bot_config = getattr(interaction.client, "config", None)
        expected_role_ids = set(role_ids or getattr(bot_config, "admin_role_ids", []))
        if not expected_role_ids:
            raise app_commands.CheckFailure("Admin role not configured.")
        if not _has_role(interaction.user, expected_role_ids):
            mentions = ", ".join(f"<@&{role_id}>" for role_id in expected_role_ids)
            raise app_commands.CheckFailure(f"You need one of these roles to run this command: {mentions}.")
        return True

    return app_commands.check(predicate)


def add_app_command_error_handler(bot: commands.Bot) -> None:
    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        logger.exception("App command error: %s", error)
        message = "An unexpected error occurred. Please try again later."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
