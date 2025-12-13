from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.economy_store import (
    EconomyData,
    add_whitelisted_role,
    get_balance,
    get_whitelisted_role_ids,
    load_economy,
    remove_whitelisted_role,
    resolve_member_team_roles,
    save_economy,
    set_balance,
)

logger = logging.getLogger(__name__)


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.economy_path = Path(self.bot.config.data_dir) / "economy.json"
        self._lock = asyncio.Lock()

    whitelist = app_commands.Group(name="whitelist", description="Manage whitelisted team roles")

    # -----------------
    # Helper utilities
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

    def _role_balance_title(self, role: discord.Role) -> str:
        suffix = "'" if role.name.endswith("s") else "'s"
        return f"{role.name}{suffix} balance"

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

    async def _send_embed(
        self, interaction: discord.Interaction, embed: discord.Embed, *, ephemeral: bool = False
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    def _ensure_whitelisted(
        self, role: discord.Role, whitelisted_ids: set[int]
    ) -> tuple[bool, discord.Embed | None]:
        if role.id in whitelisted_ids:
            return True, None
        embed = self._error_embed(
            title=":question: Team role not whitelisted",
            description=f"{role.mention} is not in the whitelisted team roles. Please whitelist it first.",
            color=0xF1C40F,
        )
        return False, embed

    def _load_data(self) -> EconomyData:
        return load_economy(self.economy_path)

    def _save_data(self, data: EconomyData) -> None:
        save_economy(self.economy_path, data)

    # -----------------
    # Whitelist commands
    # -----------------
    @whitelist.command(name="add", description="Add a role to the economy whitelist")
    @app_commands.describe(role="Role to whitelist")
    @app_commands.guild_only()
    async def whitelist_add(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not await self._require_admin(interaction):
            return

        async with self._lock:
            data = self._load_data()
            added = add_whitelisted_role(data, role.id)
            if added:
                self._save_data(data)

        if added:
            embed = self._success_embed(
                title=":white_check_mark: Role whitelisted",
                description=f"{role.mention} has been added to the team economy.",
                color=role.color,
            )
        else:
            embed = self._error_embed(
                title=":information_source: Already whitelisted",
                description=f"{role.mention} is already in the whitelist.",
                color=0x3498DB,
            )
        await self._send_embed(interaction, embed)

    @whitelist.command(name="remove", description="Remove a role from the economy whitelist")
    @app_commands.describe(role="Role to remove")
    @app_commands.guild_only()
    async def whitelist_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not await self._require_admin(interaction):
            return

        async with self._lock:
            data = self._load_data()
            removed = remove_whitelisted_role(data, role.id)
            if removed:
                self._save_data(data)

        if removed:
            embed = self._success_embed(
                title=":white_check_mark: Role removed",
                description=f"{role.mention} has been removed from the whitelist.",
                color=role.color,
            )
        else:
            embed = self._error_embed(
                title=":question: Role not found",
                description=f"{role.mention} was not in the whitelist.",
                color=0xF1C40F,
            )
        await self._send_embed(interaction, embed)

    @whitelist.command(name="list", description="Show all whitelisted team roles")
    @app_commands.guild_only()
    async def whitelist_list(self, interaction: discord.Interaction) -> None:
        if not await self._require_admin(interaction):
            return

        async with self._lock:
            data = self._load_data()
            whitelisted_ids = get_whitelisted_role_ids(data)

        if not whitelisted_ids:
            embed = self._error_embed(
                title=":information_source: No whitelisted roles",
                description="No team roles have been whitelisted yet.",
                color=0x3498DB,
            )
        else:
            roles = [interaction.guild.get_role(rid) for rid in whitelisted_ids if interaction.guild]
            mentions = [role.mention for role in roles if role]
            embed = self._success_embed(
                title=":clipboard: Whitelisted team roles",
                description="\n".join(mentions) if mentions else "No matching roles found in this server.",
                color=0x95A5A6,
            )
            embed.set_footer(text=f"Total: {len(whitelisted_ids)}")

        await self._send_embed(interaction, embed)

    # -----------------
    # Balance & payments
    # -----------------
    @app_commands.command(name="balance", description="Check a team role balance")
    @app_commands.describe(team_role="Optional team role to inspect (admin only)")
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction, team_role: discord.Role | None = None) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            embed = self._error_embed(
                title=":no_entry: Guild only",
                description="This command must be used inside a server.",
            )
            await self._send_embed(interaction, embed, ephemeral=True)
            return

        async with self._lock:
            data = self._load_data()
            whitelisted_ids = get_whitelisted_role_ids(data)

        if team_role is not None:
            if not self._has_admin_role(interaction.user):
                embed = self._error_embed(
                    title=":no_entry: You don't have permission",
                    description="You need an admin role to view other teams.",
                )
                await self._send_embed(interaction, embed, ephemeral=True)
                return

            allowed, embed_error = self._ensure_whitelisted(team_role, whitelisted_ids)
            if not allowed and embed_error:
                await self._send_embed(interaction, embed_error, ephemeral=True)
                return

            balance_value = get_balance(data, team_role.id)
            embed = discord.Embed(
                title=self._role_balance_title(team_role),
                description=f"Current Balance: **${balance_value}**",
                color=team_role.color,
            )
            await self._send_embed(interaction, embed, ephemeral=True)
            return

        team_role_resolved, error_embed = self._resolve_member_team_role(interaction.user, whitelisted_ids)
        if team_role_resolved is None and error_embed:
            await self._send_embed(interaction, error_embed, ephemeral=True)
            return

        assert team_role_resolved is not None
        balance_value = get_balance(data, team_role_resolved.id)
        embed = discord.Embed(
            title=self._role_balance_title(team_role_resolved),
            description=f"Current Balance: **${balance_value}**",
            color=team_role_resolved.color,
        )
        await self._send_embed(interaction, embed)

    @app_commands.command(name="pay", description="Transfer funds to another team role")
    @app_commands.describe(amount="Amount to transfer", team_role="Recipient team role")
    @app_commands.guild_only()
    async def pay(self, interaction: discord.Interaction, amount: int, team_role: discord.Role) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            embed = self._error_embed(
                title=":no_entry: Guild only",
                description="This command must be used inside a server.",
            )
            await self._send_embed(interaction, embed, ephemeral=True)
            return

        if amount <= 0:
            embed = self._error_embed(
                title=":warning: Invalid amount",
                description="Please provide a positive amount to transfer.",
                color=0xF1C40F,
            )
            await self._send_embed(interaction, embed, ephemeral=True)
            return

        async with self._lock:
            data = self._load_data()
            whitelisted_ids = get_whitelisted_role_ids(data)

            payer_role, error_embed = self._resolve_member_team_role(interaction.user, whitelisted_ids)
            if payer_role is None:
                embed_to_send = error_embed
            else:
                allowed, not_whitelisted_embed = self._ensure_whitelisted(team_role, whitelisted_ids)
                if not allowed:
                    embed_to_send = not_whitelisted_embed
                else:
                    payer_balance = get_balance(data, payer_role.id)
                    if payer_balance < amount:
                        embed_to_send = self._error_embed(
                            title=":no_entry: Insufficient funds",
                            description="Your team role does not have enough funds for this transfer.",
                        )
                    else:
                        set_balance(data, payer_role.id, payer_balance - amount)
                        recipient_balance = get_balance(data, team_role.id) + amount
                        set_balance(data, team_role.id, recipient_balance)
                        self._save_data(data)
                        embed_to_send = self._success_embed(
                            title=":handshake: Transaction Complete!",
                            description=f"Transferred **${amount}** to <@&{team_role.id}>",
                            color=payer_role.color,
                        )

        if embed_to_send is None:
            logger.error("Unexpected missing embed in pay command")
            return

        await self._send_embed(interaction, embed_to_send)

    # -----------------
    # Admin balance management
    # -----------------
    def _invalid_amount_embed(self, message: str) -> discord.Embed:
        return self._error_embed(
            title=":warning: Invalid amount",
            description=message,
            color=0xF1C40F,
        )

    async def _handle_balance_change(
        self,
        interaction: discord.Interaction,
        *,
        team_role: discord.Role,
        amount: int,
        show: bool,
        operation: str,
    ) -> None:
        if not await self._require_admin(interaction):
            return

        async with self._lock:
            data = self._load_data()
            whitelisted_ids = get_whitelisted_role_ids(data)
            allowed, embed_error = self._ensure_whitelisted(team_role, whitelisted_ids)
            if not allowed:
                assert embed_error is not None
                await self._send_embed(interaction, embed_error, ephemeral=not show)
                return

            old_balance = get_balance(data, team_role.id)

            if operation == "reset":
                if amount < 0:
                    embed = self._invalid_amount_embed("Amount must be greater than or equal to zero.")
                    await self._send_embed(interaction, embed, ephemeral=not show)
                    return
                new_balance = amount
            elif operation == "set":
                if amount < 0:
                    embed = self._invalid_amount_embed("Amount must be greater than or equal to zero.")
                    await self._send_embed(interaction, embed, ephemeral=not show)
                    return
                new_balance = amount
            elif operation == "add":
                if amount <= 0:
                    embed = self._invalid_amount_embed("Please provide a positive amount to add.")
                    await self._send_embed(interaction, embed, ephemeral=not show)
                    return
                new_balance = old_balance + amount
            elif operation == "remove":
                if amount <= 0:
                    embed = self._invalid_amount_embed("Please provide a positive amount to remove.")
                    await self._send_embed(interaction, embed, ephemeral=not show)
                    return
                if amount > old_balance:
                    embed = self._error_embed(
                        title=":no_entry: Insufficient funds",
                        description="Insufficient funds to remove that amount.",
                    )
                    await self._send_embed(interaction, embed, ephemeral=not show)
                    return
                new_balance = old_balance - amount
            else:
                logger.error("Unknown balance operation: %s", operation)
                return

            set_balance(data, team_role.id, new_balance)
            self._save_data(data)

        delta = new_balance - old_balance
        if operation in {"reset", "set"}:
            sign = "+" if delta >= 0 else "-"
            change_line = f"Change: {sign}${abs(delta)}"
        elif operation == "add":
            change_line = f"Change: +${amount}"
        else:
            change_line = f"Change: -${amount}"

        embed = self._success_embed(
            title=":white_check_mark: Balance updated",
            description=(
                f"Team: <@&{team_role.id}>\n"
                f"Old: **${old_balance}**\n"
                f"New: **${new_balance}**\n"
                f"{change_line}"
            ),
            color=team_role.color,
        )
        await self._send_embed(interaction, embed, ephemeral=not show)

    @app_commands.command(name="reset_balance", description="Reset a team role balance")
    @app_commands.describe(team_role="Team role to reset", amount="Amount to reset to", show="Show publicly")
    @app_commands.guild_only()
    async def reset_balance(
        self, interaction: discord.Interaction, team_role: discord.Role, amount: int, show: bool | None = False
    ) -> None:
        await self._handle_balance_change(
            interaction,
            team_role=team_role,
            amount=amount,
            show=bool(show),
            operation="reset",
        )

    @app_commands.command(name="set_balance", description="Set a team role balance")
    @app_commands.describe(team_role="Team role to set", amount="Amount to set", show="Show publicly")
    @app_commands.guild_only()
    async def set_balance(
        self, interaction: discord.Interaction, team_role: discord.Role, amount: int, show: bool | None = False
    ) -> None:
        await self._handle_balance_change(
            interaction,
            team_role=team_role,
            amount=amount,
            show=bool(show),
            operation="set",
        )

    @app_commands.command(name="add_balance", description="Add funds to a team role balance")
    @app_commands.describe(team_role="Team role to add to", amount="Amount to add", show="Show publicly")
    @app_commands.guild_only()
    async def add_balance(
        self, interaction: discord.Interaction, team_role: discord.Role, amount: int, show: bool | None = False
    ) -> None:
        await self._handle_balance_change(
            interaction,
            team_role=team_role,
            amount=amount,
            show=bool(show),
            operation="add",
        )

    @app_commands.command(name="remove_balance", description="Remove funds from a team role balance")
    @app_commands.describe(team_role="Team role to remove from", amount="Amount to remove", show="Show publicly")
    @app_commands.guild_only()
    async def remove_balance(
        self, interaction: discord.Interaction, team_role: discord.Role, amount: int, show: bool | None = False
    ) -> None:
        await self._handle_balance_change(
            interaction,
            team_role=team_role,
            amount=amount,
            show=bool(show),
            operation="remove",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
