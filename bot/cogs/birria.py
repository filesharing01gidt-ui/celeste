from __future__ import annotations

import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

BIRRIA_PREFIX = "?birria-"
BIRRIA_ANSWER = "LOFNKAJHBEC"
ALLOWED_CHARS = set("ABCDEFGHIJKLMNOP")


class BirriaTrivia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _error_embed(self, description: str) -> discord.Embed:
        return discord.Embed(title=":warning: Invalid guess", description=description, color=0xE74C3C)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        content = message.content
        if not content.startswith(BIRRIA_PREFIX):
            return

        raw_guess = content[len(BIRRIA_PREFIX) :]
        if not raw_guess or not raw_guess.isascii() or not raw_guess.isalpha():
            await message.reply(
                embed=self._error_embed("Invalid character detected. Only letters A–P are allowed."),
                mention_author=False,
            )
            return

        guess = raw_guess.upper()

        if len(guess) != 11:
            await message.reply(
                embed=self._error_embed("Invalid length. Guess must be exactly 11 letters."),
                mention_author=False,
            )
            return

        if any(char not in ALLOWED_CHARS for char in guess):
            await message.reply(
                embed=self._error_embed("Invalid character detected. Only letters A–P are allowed."),
                mention_author=False,
            )
            return

        if len(set(guess)) != len(guess):
            await message.reply(embed=self._error_embed("All letters must be unique."), mention_author=False)
            return

        if raw_guess != raw_guess.upper():
            await message.reply(
                embed=self._error_embed("Invalid character detected. Only letters A–P are allowed."),
                mention_author=False,
            )
            return

        score = sum(1 for guess_char, answer_char in zip(guess, BIRRIA_ANSWER) if guess_char == answer_char)
        if guess == BIRRIA_ANSWER:
            score = 9

        await message.reply(f"## {score}/11", mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BirriaTrivia(bot))
