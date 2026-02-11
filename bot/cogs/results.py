"""Results cog — /results, /status commands, and result publishing logic."""

import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot import database as db
from bot.views.runoff_view import RunoffView

log = logging.getLogger("demobot.results")


async def publish_results(bot: commands.Bot, cycle_id: int) -> None:
    """Calculate and publish results for a cycle. Handles ties with runoff."""
    config = bot.config
    channel = bot.get_channel(config.vote_channel_id)
    if not channel:
        log.error(f"Vote channel {config.vote_channel_id} not found!")
        return

    results = db.calculate_results(cycle_id)
    if not results:
        db.close_cycle(cycle_id)
        await channel.send(
            embed=discord.Embed(
                title="Voting Cycle Closed",
                description="No votes were cast this cycle.",
                color=discord.Color.greyple(),
            )
        )
        return

    # Check for tie at the top
    top_score = results[0]["avg_score"]
    tied = [r for r in results if abs(r["avg_score"] - top_score) < 0.0001]

    if len(tied) > 1:
        # Start runoff
        await start_runoff(bot, cycle_id, tied, results, channel)
    else:
        # Clear winner
        winner = results[0]
        db.close_cycle(cycle_id)
        db.publish_cycle(cycle_id, winner["game_id"])
        embed = build_results_embed(cycle_id, results, winner, config.carry_over_count)
        await channel.send(embed=embed)
        log.info(f"Cycle #{cycle_id} results published. Winner: {winner['game_name']}")


DAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def compute_runoff_deadline(config) -> datetime:
    """Compute the next occurrence of the runoff deadline day+time."""
    now = datetime.now(config.tz)
    deadline_day_num = DAY_MAP.get(config.runoff_deadline_day, 0)
    deadline_h, deadline_m = map(int, config.runoff_deadline_time.split(":"))

    days_until = (deadline_day_num - now.weekday()) % 7
    deadline = now.replace(
        hour=deadline_h, minute=deadline_m, second=0, microsecond=0
    ) + timedelta(days=days_until)

    # If the deadline is in the past (or right now), push to next week
    if deadline <= now:
        deadline += timedelta(days=7)

    return deadline


async def start_runoff(
    bot: commands.Bot,
    cycle_id: int,
    tied: list[dict],
    full_results: list[dict],
    channel: discord.TextChannel,
) -> None:
    """Start a runoff vote for tied games. Resolution is handled by the scheduler."""
    config = bot.config
    db.set_cycle_runoff(cycle_id)

    tied_games = [(g["game_id"], g["game_name"]) for g in tied]
    view = RunoffView(cycle_id, tied_games)
    embed = view.build_embed()

    deadline = compute_runoff_deadline(config)
    discord_ts = int(deadline.timestamp())
    embed.add_field(
        name="Runoff Deadline",
        value=f"<t:{discord_ts}:F> (<t:{discord_ts}:R>)",
        inline=False,
    )

    msg = await channel.send(embed=embed, view=view)
    view.message_id = msg.id

    # Notify attending members
    attending = db.get_attending_users(cycle_id)
    for uid in attending:
        try:
            user = await bot.fetch_user(uid)
            await user.send(
                f"A **runoff vote** is needed for MAVV Game Night! "
                f"Head to <#{config.vote_channel_id}> to cast your tie-breaker vote. "
                f"Runoff closes <t:{discord_ts}:F> (<t:{discord_ts}:R>)."
            )
        except Exception:
            pass

    # Schedule the runoff resolution via the scheduler cog
    scheduler_cog = bot.get_cog("Scheduler")
    if scheduler_cog:
        scheduler_cog.schedule_runoff_resolution(cycle_id, deadline)
    else:
        log.error("Scheduler cog not found — runoff will not auto-resolve!")


async def resolve_runoff(
    bot: commands.Bot,
    cycle_id: int,
    full_results: list[dict],
    channel: discord.TextChannel,
) -> None:
    """Count runoff votes and publish final results."""
    runoff_results = db.get_runoff_results(cycle_id)

    if not runoff_results:
        # No runoff votes cast — pick first tied game alphabetically
        top_score = full_results[0]["avg_score"]
        tied = [r for r in full_results if abs(r["avg_score"] - top_score) < 0.0001]
        winner = min(tied, key=lambda g: g["game_name"])
        note = "No runoff votes were cast. Winner chosen alphabetically from tied games."
    else:
        # Check for runoff tie
        max_votes = runoff_results[0]["vote_count"]
        runoff_tied = [r for r in runoff_results if r["vote_count"] == max_votes]

        if len(runoff_tied) > 1:
            # Still tied — pick alphabetically
            winner_row = min(runoff_tied, key=lambda g: g["game_name"])
            note = "Runoff also tied! Winner chosen alphabetically."
        else:
            winner_row = runoff_results[0]
            note = None

        # Find the full result entry for the winner
        winner = next(
            (r for r in full_results if r["game_id"] == winner_row["game_id"]),
            {"game_id": winner_row["game_id"], "game_name": winner_row["game_name"], "avg_score": 0, "vote_count": 0},
        )

    db.close_cycle(cycle_id)
    db.publish_cycle(cycle_id, winner["game_id"])

    config = bot.config
    embed = build_results_embed(cycle_id, full_results, winner, config.carry_over_count)
    if note:
        embed.add_field(name="Note", value=note, inline=False)

    # Add runoff breakdown
    if runoff_results:
        breakdown = "\n".join(
            f"**{r['game_name']}**: {r['vote_count']} vote{'s' if r['vote_count'] != 1 else ''}"
            for r in runoff_results
        )
        embed.add_field(name="Runoff Results", value=breakdown, inline=False)

    await channel.send(embed=embed)
    log.info(f"Cycle #{cycle_id} runoff resolved. Winner: {winner['game_name']}")


def build_results_embed(
    cycle_id: int, results: list[dict], winner: dict, carry_over_count: int = 5
) -> discord.Embed:
    """Build the results announcement embed."""
    embed = discord.Embed(
        title="MAVV Game Night Results",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Winner",
        value=f"**{winner['game_name']}**",
        inline=False,
    )

    # Figure out the carry-over cutoff, expanding for ties at the boundary
    cutoff = min(carry_over_count, len(results))
    if cutoff > 0 and cutoff < len(results):
        boundary_score = results[cutoff - 1]["avg_score"]
        while cutoff < len(results) and abs(results[cutoff]["avg_score"] - boundary_score) < 0.0001:
            cutoff += 1

    ranking_lines = []
    for i, r in enumerate(results):
        medal = ""
        if i == 0:
            medal = "\U0001f947 "
        elif i == 1:
            medal = "\U0001f948 "
        elif i == 2:
            medal = "\U0001f949 "

        avg = r["avg_score"]
        votes = r["vote_count"]
        ranking_lines.append(
            f"{medal}**{i+1}.** {r['game_name']} — avg score: {avg:.2f} ({votes} votes)"
        )

        if i == cutoff - 1 and cutoff < len(results):
            ranking_lines.append("─── *returning next week* ───")

    embed.add_field(
        name="Full Rankings",
        value="\n".join(ranking_lines),
        inline=False,
    )

    attending = db.get_attending_users(cycle_id)
    embed.set_footer(text=f"Cycle #{cycle_id} | {len(attending)} attending members voted")
    return embed


class Results(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="results", description="View the latest game night results"
    )
    async def results(self, interaction: discord.Interaction) -> None:
        conn = db.get_connection()
        cycle = conn.execute(
            "SELECT * FROM voting_cycles WHERE status = 'published' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not cycle:
            await interaction.response.send_message(
                "No published results yet.", ephemeral=True
            )
            return

        results_data = db.calculate_results(cycle["id"])
        if not results_data:
            await interaction.response.send_message(
                "No vote data for the latest cycle.", ephemeral=True
            )
            return

        game = db.get_game_by_id(cycle["winning_game_id"])
        winner = next(
            (r for r in results_data if r["game_id"] == cycle["winning_game_id"]),
            {"game_id": cycle["winning_game_id"], "game_name": game["name"] if game else "Unknown", "avg_score": 0, "vote_count": 0},
        )

        embed = build_results_embed(cycle["id"], results_data, winner, self.bot.config.carry_over_count)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="status", description="See the current voting cycle status"
    )
    async def status(self, interaction: discord.Interaction) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            latest = db.get_latest_cycle()
            if latest:
                await interaction.response.send_message(
                    f"No active cycle. Last cycle was #{latest['id']} ({latest['status']}). "
                    "Next cycle will open automatically on schedule.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "No voting cycles have been created yet.", ephemeral=True
                )
            return

        games = db.get_cycle_games(cycle["id"])
        attending = db.get_attending_users(cycle["id"])
        voters = db.get_voters(cycle["id"])
        all_attendance = db.get_all_attendance(cycle["id"])

        embed = discord.Embed(
            title=f"Voting Cycle #{cycle['id']} — {cycle['status'].title()}",
            color=discord.Color.blue(),
        )

        if games:
            game_list = "\n".join(f"- {g['game_name']}" for g in games)
            embed.add_field(name=f"Games ({len(games)})", value=game_list, inline=False)

        att_yes = [a for a in all_attendance if a["attending"]]
        att_no = [a for a in all_attendance if not a["attending"]]
        embed.add_field(
            name="Attendance",
            value=f"Attending: {len(att_yes)} | Not attending: {len(att_no)}",
            inline=True,
        )

        non_voters = [uid for uid in attending if uid not in voters]
        embed.add_field(
            name="Votes",
            value=f"Submitted: {len(voters)} | Waiting on: {len(non_voters)}",
            inline=True,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Results(bot))
