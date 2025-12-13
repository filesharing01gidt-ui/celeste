from __future__ import annotations

import logging
from typing import Callable

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


def _has_role(member: discord.Member, role_name: str) -> bool:
    return any(role.name == role_name for role in member.roles)


def prefix_admin_check(role_name: str | None = None) -> Callable[[commands.Context], bool]:
    def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.CheckFailure("This command can only be used in a server.")
        assert isinstance(ctx.author, discord.Member)
        expected_role = role_name or getattr(getattr(ctx.bot, "config", None), "admin_role_name", None)
        if expected_role is None:
            raise commands.CheckFailure("Admin role not configured.")
        if not _has_role(ctx.author, expected_role):
            raise commands.CheckFailure(f"You need the '{expected_role}' role to run this command.")
        return True

    return commands.check(predicate)


def app_command_admin_check(role_name: str | None = None) -> Callable[[app_commands.Command], app_commands.Command]:
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("This command can only be used in a server.")
        expected_role = role_name or getattr(getattr(interaction.client, "config", None), "admin_role_name", None)
        if expected_role is None:
            raise app_commands.CheckFailure("Admin role not configured.")
        if not _has_role(interaction.user, expected_role):
            raise app_commands.CheckFailure(f"You need the '{expected_role}' role to run this command.")
        return True

    def decorator(command: app_commands.Command) -> app_commands.Command:
        return command.check(predicate)

    return decorator


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
