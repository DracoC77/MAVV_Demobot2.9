"""Scheduler cog — automated weekly cycle management using APScheduler."""

import logging
from datetime import datetime, timedelta

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord.ext import commands

from bot import database as db
from bot.cogs.admin import build_cycle_announcement
from bot.cogs.results import publish_results
from bot.views.vote_view import VoteNowButton

log = logging.getLogger("demobot.scheduler")

DAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def day_to_cron(day_name: str) -> str:
    """Convert day name to cron day_of_week (mon=0 ... sun=6 or mon-sun)."""
    return day_name[:3]


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scheduler = AsyncIOScheduler()

    async def cog_load(self) -> None:
        config = self.bot.config
        tz = config.tz

        # Parse times
        open_h, open_m = map(int, config.vote_open_time.split(":"))
        results_h, results_m = map(int, config.results_time.split(":"))
        reminder_h, reminder_m = map(int, config.reminder_time.split(":"))

        # Schedule: open voting
        self.scheduler.add_job(
            self.open_voting,
            CronTrigger(
                day_of_week=day_to_cron(config.vote_open_day),
                hour=open_h,
                minute=open_m,
                timezone=tz,
            ),
            id="open_voting",
            replace_existing=True,
        )

        # Schedule: publish results
        self.scheduler.add_job(
            self.close_voting,
            CronTrigger(
                day_of_week=day_to_cron(config.results_day),
                hour=results_h,
                minute=results_m,
                timezone=tz,
            ),
            id="close_voting",
            replace_existing=True,
        )

        # Schedule: reminders
        self.scheduler.add_job(
            self.send_reminders,
            CronTrigger(
                day_of_week=day_to_cron(config.reminder_day),
                hour=reminder_h,
                minute=reminder_m,
                timezone=tz,
            ),
            id="send_reminders",
            replace_existing=True,
        )

        self.scheduler.start()
        log.info(
            f"Scheduler started. "
            f"Open: {config.vote_open_day} {config.vote_open_time}, "
            f"Results: {config.results_day} {config.results_time}, "
            f"Reminders: {config.reminder_day} {config.reminder_time} "
            f"(tz={config.timezone})"
        )

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)

    async def open_voting(self) -> None:
        """Automatically open a new voting cycle."""
        log.info("Scheduled: opening new voting cycle.")
        config = self.bot.config

        # Don't open if there's already an active cycle
        existing = db.get_current_cycle()
        if existing:
            log.info(f"Skipping auto-open: cycle #{existing['id']} is still active.")
            return

        cycle_id = db.create_cycle()

        # Carry over from last published cycle
        conn = db.get_connection()
        prev = conn.execute(
            "SELECT * FROM voting_cycles WHERE id < ? AND status = 'published' ORDER BY id DESC LIMIT 1",
            (cycle_id,),
        ).fetchone()
        conn.close()

        if prev:
            top_games = db.get_top_games_from_cycle(prev["id"], config.carry_over_count)
            for game in top_games:
                db.add_game_to_cycle(cycle_id, game["game_id"], is_carry_over=True)

        # Post announcement
        channel = self.bot.get_channel(config.vote_channel_id)
        if not channel:
            log.error(f"Vote channel {config.vote_channel_id} not found.")
            return

        games = db.get_cycle_games(cycle_id)
        embed = build_cycle_announcement(cycle_id, games, config)

        # Add schedule info with Discord timestamps
        now = datetime.now(config.tz)
        results_day_num = DAY_MAP.get(config.results_day, 4)
        days_until = (results_day_num - now.weekday()) % 7
        if days_until == 0 and now.hour >= int(config.results_time.split(":")[0]):
            days_until = 7
        results_dt = now.replace(
            hour=int(config.results_time.split(":")[0]),
            minute=int(config.results_time.split(":")[1]),
            second=0,
            microsecond=0,
        )
        results_dt = results_dt + timedelta(days=days_until)
        discord_ts = int(results_dt.timestamp())
        embed.add_field(
            name="Results",
            value=f"<t:{discord_ts}:F> (<t:{discord_ts}:R>)",
            inline=False,
        )

        view = VoteNowButton()
        msg = await channel.send(embed=embed, view=view)
        db.set_cycle_announcement_message(cycle_id, msg.id)
        log.info(f"Cycle #{cycle_id} opened and announced.")

    async def close_voting(self) -> None:
        """Automatically close voting and publish results."""
        log.info("Scheduled: closing voting and publishing results.")
        cycle = db.get_current_cycle()
        if not cycle:
            log.info("No active cycle to close.")
            return

        if cycle["status"] == "runoff":
            log.info("Cycle is in runoff — will be resolved by runoff timer.")
            return

        await publish_results(self.bot, cycle["id"])

    async def send_reminders(self) -> None:
        """Send DM reminders to attending members who haven't voted."""
        log.info("Scheduled: sending vote reminders.")
        config = self.bot.config
        cycle = db.get_current_cycle()
        if not cycle or cycle["status"] != "open":
            log.info("No open cycle for reminders.")
            return

        attending = db.get_attending_users(cycle["id"])
        voters = db.get_voters(cycle["id"])
        non_voters = [uid for uid in attending if uid not in voters]

        sent = 0
        for uid in non_voters:
            try:
                user = await self.bot.fetch_user(uid)
                await user.send(
                    f"Hey! Friendly reminder that you haven't submitted your MAVV Game Night "
                    f"vote yet. Head to <#{config.vote_channel_id}> and click **Vote Now** "
                    f"or use `/vote` to rank this week's games before results drop!"
                )
                sent += 1
            except Exception as e:
                log.warning(f"Failed to DM user {uid}: {e}")

        log.info(f"Reminders sent to {sent}/{len(non_voters)} non-voters.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Scheduler(bot))
