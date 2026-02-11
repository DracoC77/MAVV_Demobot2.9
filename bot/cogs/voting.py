"""Voting cog — /vote, /attend, /nominate commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import database as db
from bot.views.vote_view import start_vote_flow

log = logging.getLogger("demobot.voting")

NOT_AUTHORIZED_MSG = (
    "You're not on the authorized voters list for MAVV Game Night. "
    "Ask an admin to add you with `/admin adduser`."
)


class Voting(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="vote", description="Rank this week's games for MAVV Game Night")
    async def vote(self, interaction: discord.Interaction) -> None:
        if not db.is_authorized(interaction.user.id):
            await interaction.response.send_message(NOT_AUTHORIZED_MSG, ephemeral=True)
            return

        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "There's no active voting cycle right now.", ephemeral=True
            )
            return

        if cycle["status"] == "runoff":
            await interaction.response.send_message(
                "This cycle is in a **runoff vote** between tied games! "
                f"Look for the runoff message in <#{self.bot.config.vote_channel_id}> "
                "and click a button to cast your tie-breaker vote.",
                ephemeral=True,
            )
            return

        if cycle["status"] != "open":
            await interaction.response.send_message(
                "Voting is not currently open.",
                ephemeral=True,
            )
            return

        await start_vote_flow(interaction, dict(cycle))

    @app_commands.command(
        name="attend", description="Set your attendance for this week's game night"
    )
    @app_commands.describe(status="Are you attending game night this week?")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Yes — I'm playing!", value="yes"),
            app_commands.Choice(name="No — can't make it", value="no"),
        ]
    )
    async def attend(self, interaction: discord.Interaction, status: str) -> None:
        if not db.is_authorized(interaction.user.id):
            await interaction.response.send_message(NOT_AUTHORIZED_MSG, ephemeral=True)
            return

        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "There's no active voting cycle right now.", ephemeral=True
            )
            return

        attending = status == "yes"
        db.set_attendance(cycle["id"], interaction.user.id, attending)

        if attending:
            msg = (
                "You're marked as **attending** this week!\n"
                "Use `/vote` to rank the games, or click the **Vote Now** button "
                "on the announcement."
            )
        else:
            msg = (
                "You're marked as **not attending** this week.\n"
                "You can change this anytime before results are published with `/attend yes`."
            )

        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="nominate", description="Nominate a game for next week's ballot"
    )
    @app_commands.describe(game="Name of the game you want to nominate")
    async def nominate(self, interaction: discord.Interaction, game: str) -> None:
        if not db.is_authorized(interaction.user.id):
            await interaction.response.send_message(NOT_AUTHORIZED_MSG, ephemeral=True)
            return

        config = self.bot.config

        # Check user's pending nomination count
        user_noms = db.get_pending_nomination_count_for_user(interaction.user.id)
        if user_noms >= config.max_nominations_per_person:
            await interaction.response.send_message(
                f"You've already used your nomination "
                f"({config.max_nominations_per_person} per person). "
                "Your slot resets when the next voting cycle opens.",
                ephemeral=True,
            )
            return

        game_name = game.strip()
        if not game_name:
            await interaction.response.send_message(
                "Please provide a game name.", ephemeral=True
            )
            return

        game_id = db.get_or_create_game(game_name, added_by=interaction.user.id)
        added = db.add_pending_nomination(game_id, nominated_by=interaction.user.id)

        if not added:
            await interaction.response.send_message(
                f"**{game_name}** has already been nominated.", ephemeral=True
            )
            return

        # Announce in channel
        channel = self.bot.get_channel(config.vote_channel_id)
        if channel:
            await channel.send(
                f"**{interaction.user.display_name}** nominated **{game_name}** for next week!"
            )

        await interaction.response.send_message(
            f"**{game_name}** has been nominated and will appear on next week's ballot!",
            ephemeral=True,
        )

    @app_commands.command(name="myvote", description="See your current vote for this week")
    async def myvote(self, interaction: discord.Interaction) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            cycle = db.get_latest_cycle()
        if not cycle:
            await interaction.response.send_message(
                "No voting cycles found.", ephemeral=True
            )
            return

        votes = db.get_user_votes(cycle["id"], interaction.user.id)
        runoff_vote = db.get_user_runoff_vote(cycle["id"], interaction.user.id)

        if not votes and not runoff_vote:
            await interaction.response.send_message(
                "You haven't voted in the current cycle yet.", ephemeral=True
            )
            return

        attendance = db.get_attendance(cycle["id"], interaction.user.id)
        att_status = "Attending" if attendance else "Not attending"
        status_label = cycle["status"].title()
        if cycle["status"] == "runoff" and cycle["runoff_round"] and cycle["runoff_round"] > 1:
            status_label = f"Runoff Round {cycle['runoff_round']}"

        embed = discord.Embed(
            title="Your Current Vote",
            color=discord.Color.blue(),
        )

        if votes:
            ranking_text = "\n".join(
                f"**{i+1}.** {v['game_name']} ({v['rank']} pts)" for i, v in enumerate(votes)
            )
            embed.add_field(name="Game Ranking", value=ranking_text, inline=False)

        if runoff_vote:
            embed.add_field(
                name="Runoff Pick",
                value=f"**{runoff_vote['game_name']}**",
                inline=False,
            )

        embed.set_footer(text=f"Attendance: {att_status} | Cycle #{cycle['id']} ({status_label})")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voting(bot))
