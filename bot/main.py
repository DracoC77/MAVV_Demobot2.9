"""MAVV Demobot 2.9 — Discord Game Night Voting Bot."""

import logging
import sys

import discord
from discord.ext import commands

from bot.config import Config
from bot import database as db
from bot.database import init_db
from bot.views.vote_view import VoteNowButton
from bot.views.runoff_view import RunoffView

log = logging.getLogger("demobot")


class Demobot(commands.Bot):
    """Main bot class with config attached."""

    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            activity=discord.Game(name="MAVV Game Night"),
        )
        self.config = config

    async def setup_hook(self) -> None:
        # Register persistent views (survive bot restarts)
        self.add_view(VoteNowButton())

        # Re-register runoff view if a cycle is currently in runoff
        cycle = db.get_current_cycle()
        if cycle and cycle["status"] == "runoff":
            results = db.calculate_results(cycle["id"])
            if results:
                top_score = results[0]["avg_score"]
                tied = [r for r in results if abs(r["avg_score"] - top_score) < 0.0001]
                if len(tied) > 1:
                    tied_games = [(g["game_id"], g["game_name"]) for g in tied]
                    self.add_view(RunoffView(cycle["id"], tied_games))

        # Load cogs
        await self.load_extension("bot.cogs.voting")
        await self.load_extension("bot.cogs.admin")
        await self.load_extension("bot.cogs.results")
        await self.load_extension("bot.cogs.scheduler")

        # Sync slash commands
        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        log.info("Slash commands synced.")

    async def on_ready(self) -> None:
        log.info(f"Demobot online as {self.user} (ID: {self.user.id})")
        log.info(f"Guild ID: {self.config.guild_id}")
        log.info(f"Vote channel ID: {self.config.vote_channel_id}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    config = Config.from_env()
    init_db()

    bot = Demobot(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
