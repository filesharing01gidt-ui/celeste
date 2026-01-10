from __future__ import annotations

import logging
import random
import re
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

WORDLE_PREFIX = "?wordle"
WORDLE_PATTERN = re.compile(r"^\?wordle(\d+)-([A-Za-z]+)\s*$")
WORDLE_SOLUTIONS = {
    1: "KAHVE",
    2: "BAZAAR",
    3: "OTTOMAN",
}

EMOJI_GREEN = "🟩"
EMOJI_YELLOW = "🟨"
EMOJI_GRAY = "⬜"
EMOJI_CHECK = "✅"


class LyingWordle(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.wordlist = self._load_wordlist()

    def _error_embed(self, title: str, description: str, *, color: int = 0xE74C3C) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        return embed

    def _load_wordlist(self) -> set[str]:
        wordlist_path = Path(__file__).resolve().parent.parent / "assets" / "wordlist_5_7.txt"
        words: set[str] = set()
        try:
            with wordlist_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or not line.isalpha():
                        continue
                    if not 5 <= len(line) <= 7:
                        continue
                    words.add(line.upper())
        except FileNotFoundError:
            logger.warning("Wordle wordlist not found at %s", wordlist_path)
        return words

    def _true_feedback(self, guess: str, solution: str) -> list[str]:
        result = [EMOJI_GRAY] * len(guess)
        remaining: dict[str, int] = {}
        for idx, (g_char, s_char) in enumerate(zip(guess, solution)):
            if g_char == s_char:
                result[idx] = EMOJI_GREEN
            else:
                remaining[s_char] = remaining.get(s_char, 0) + 1

        for idx, g_char in enumerate(guess):
            if result[idx] == EMOJI_GREEN:
                continue
            if remaining.get(g_char, 0) > 0:
                result[idx] = EMOJI_YELLOW
                remaining[g_char] -= 1
        return result

    def _apply_lie(self, emojis: list[str], wordle_id: int, guess: str) -> list[str]:
        if len(emojis) <= 2:
            return emojis
        rng = random.Random(f"{wordle_id}:{guess}")
        lie_index = rng.randrange(1, len(emojis) - 1)
        options = [EMOJI_GREEN, EMOJI_YELLOW, EMOJI_GRAY]
        true_emoji = emojis[lie_index]
        options.remove(true_emoji)
        emojis[lie_index] = rng.choice(options)
        return emojis

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        content = message.content.strip()
        if not content.startswith(WORDLE_PREFIX):
            return

        match = WORDLE_PATTERN.match(content)
        if not match:
            return

        wordle_id = int(match.group(1))
        guess = match.group(2).upper()

        solution = WORDLE_SOLUTIONS.get(wordle_id)
        if solution is None:
            await message.reply(
                embed=self._error_embed(":warning: Unknown Wordle ID", "Please check the Wordle ID and try again.")
            )
            return

        if len(guess) != len(solution):
            await message.reply(
                embed=self._error_embed(
                    ":warning: Invalid length", f"Guess must be {len(solution)} letters."
                )
            )
            return

        if guess not in self.wordlist:
            await message.reply(embed=self._error_embed(":warning: Invalid word", "Invalid word."))
            return

        true_emojis = self._true_feedback(guess, solution)
        if guess == solution:
            await message.reply(EMOJI_CHECK * len(solution))
            return

        lied_emojis = self._apply_lie(true_emojis, wordle_id, guess)
        await message.reply("".join(lied_emojis))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LyingWordle(bot))
