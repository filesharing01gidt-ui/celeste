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
        self._leaderboard_page_size = 10


class LeaderboardView(discord.ui.View):
    def __init__(
        self,
        *,
        entries: list[tuple[str, int, int]],
        page_size: int,
        invoker_id: int,
        make_embed,
    ):
        super().__init__(timeout=180)
        self.entries = entries
        self.page_size = page_size
        self.page = 0
        self.invoker_id = invoker_id
        self.make_embed = make_embed
        self._update_buttons()

    def _page_count(self) -> int:
        if not self.entries:
            return 1
        return max(1, (len(self.entries) + self.page_size - 1) // self.page_size)

    def _update_buttons(self) -> None:
        total = self._page_count()
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "leaderboard_prev":
                    child.disabled = self.page <= 0
                elif child.custom_id == "leaderboard_next":
                    child.disabled = self.page >= total - 1

    def _slice_entries(self) -> list[tuple[str, int, int]]:
        start = self.page * self.page_size
        end = start + self.page_size
        return self.entries[start:end]

    async def _ensure_invoker(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=":no_entry: Not allowed",
                    description="Only the command invoker can use these buttons.",
                    color=0xE74C3C,
                ),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="leaderboard_prev")
    async def on_prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._ensure_invoker(interaction):
            return
        if self.page > 0:
            self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(self.page, self._page_count(), self._slice_entries()), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="leaderboard_next")
    async def on_next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._ensure_invoker(interaction):
            return
        if self.page < self._page_count() - 1:
            self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(self.page, self._page_count(), self._slice_entries()), view=self)

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
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        *,
        ephemeral: bool = False,
        content: str | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral, content=content, view=view)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral, content=content, view=view)

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

    def _make_leaderboard_embed(
        self, page: int, page_count: int, entries: list[tuple[str, int, int]]
    ) -> discord.Embed:
        lines: list[str] = []
        rank_offset = page * self._leaderboard_page_size
        if not entries:
            lines.append("No balances found.")
        else:
            for idx, (display, balance, role_id) in enumerate(entries, start=1):
                rank = rank_offset + idx
                lines.append(f"#{rank} {display} — ${balance:,}")

        embed = self._success_embed(
            title=":trophy: Leaderboard",
            description="\n".join(lines),
            color=0x2ECC71,
        )
        embed.set_footer(text=f"Page {page + 1} / {page_count}")
        return embed

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
    @app_commands.command(name="leaderboard", description="Show the team role balance leaderboard")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if not await self._require_admin(interaction):
            return

        async with self._lock:
            data = self._load_data()
            balances = {int(role_id): amount for role_id, amount in data.balances.items()}

        if interaction.guild is None:
            embed = self._error_embed(
                title=":no_entry: Guild only",
                description="This command must be used inside a server.",
            )
            await self._send_embed(interaction, embed, ephemeral=True)
            return

        entries: list[tuple[str, int, int]] = []
        for role_id, amount in balances.items():
            if amount is None:
                continue
            role = interaction.guild.get_role(role_id)
            display = role.mention if role else f"Role {role_id}"
            entries.append((display, int(amount), role_id))

        entries.sort(key=lambda item: item[1], reverse=True)

        page_count = max(1, (len(entries) + self._leaderboard_page_size - 1) // self._leaderboard_page_size)
        initial_entries = entries[: self._leaderboard_page_size]
        embed = self._make_leaderboard_embed(0, page_count, initial_entries)
        view = LeaderboardView(
            entries=entries,
            page_size=self._leaderboard_page_size,
            invoker_id=interaction.user.id,
            make_embed=lambda page, total, slice_entries: self._make_leaderboard_embed(page, total, slice_entries),
        )
        await self._send_embed(interaction, embed, ephemeral=True, view=view)

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
            title = self._role_balance_title(team_role)
            embed = discord.Embed(
                title=title,
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
        title = self._role_balance_title(team_role_resolved)
        embed = discord.Embed(
            title=title,
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
                            color=team_role.color,
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
                await self._send_embed(interaction, embed_error, ephemeral=False)
                return

            old_balance = get_balance(data, team_role.id)

            if operation == "reset":
                if amount < 0:
                    embed = self._invalid_amount_embed("Amount must be greater than or equal to zero.")
                    await self._send_embed(interaction, embed, ephemeral=False)
                    return
                new_balance = amount
            elif operation == "set":
                if amount < 0:
                    embed = self._invalid_amount_embed("Amount must be greater than or equal to zero.")
                    await self._send_embed(interaction, embed, ephemeral=False)
                    return
                new_balance = amount
            elif operation == "add":
                if amount <= 0:
                    embed = self._invalid_amount_embed("Please provide a positive amount to add.")
                    await self._send_embed(interaction, embed, ephemeral=False)
                    return
                new_balance = old_balance + amount
            elif operation == "remove":
                if amount <= 0:
                    embed = self._invalid_amount_embed("Please provide a positive amount to remove.")
                    await self._send_embed(interaction, embed, ephemeral=False)
                    return
                if amount > old_balance:
                    embed = self._error_embed(
                        title=":no_entry: Insufficient funds",
                        description="Insufficient funds to remove that amount.",
                    )
                    await self._send_embed(interaction, embed, ephemeral=False)
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
        await self._send_embed(interaction, embed, ephemeral=False)

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
