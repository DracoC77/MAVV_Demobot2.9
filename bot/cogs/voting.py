"""Voting cog — /vote, /attend, /nominate commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import database as db
from bot.views.vote_view import start_vote_flow

log = logging.getLogger("demobot.voting")


class Voting(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="vote", description="Rank this week's games for MAVV Game Night")
    async def vote(self, interaction: discord.Interaction) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "There's no active voting cycle right now.", ephemeral=True
            )
            return

        if cycle["status"] != "open":
            await interaction.response.send_message(
                "Voting is not currently open. The cycle may be in a runoff or closed.",
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
        name="nominate", description="Nominate a game for this week's ballot"
    )
    @app_commands.describe(game="Name of the game you want to nominate")
    async def nominate(self, interaction: discord.Interaction, game: str) -> None:
        config = self.bot.config
        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "There's no active voting cycle right now.", ephemeral=True
            )
            return

        if cycle["status"] != "open":
            await interaction.response.send_message(
                "Nominations are closed for this cycle.", ephemeral=True
            )
            return

        # Check total game count
        total = db.get_cycle_game_count(cycle["id"])
        if total >= config.max_total_games:
            await interaction.response.send_message(
                f"The ballot is full ({config.max_total_games} games max). "
                "No more nominations this week.",
                ephemeral=True,
            )
            return

        # Check user's nomination count
        user_noms = db.get_user_nomination_count(cycle["id"], interaction.user.id)
        if user_noms >= config.max_nominations_per_person:
            await interaction.response.send_message(
                f"You've already used your nomination for this week "
                f"({config.max_nominations_per_person} per person).",
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
        added = db.add_game_to_cycle(
            cycle["id"], game_id, is_carry_over=False, nominated_by=interaction.user.id
        )

        if not added:
            await interaction.response.send_message(
                f"**{game_name}** is already on this week's ballot.", ephemeral=True
            )
            return

        # Announce in channel
        channel = self.bot.get_channel(config.vote_channel_id)
        if channel:
            await channel.send(
                f"**{interaction.user.display_name}** nominated **{game_name}** for this week!"
            )

        await interaction.response.send_message(
            f"**{game_name}** has been added to this week's ballot!", ephemeral=True
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
        if not votes:
            await interaction.response.send_message(
                "You haven't voted in the current cycle yet.", ephemeral=True
            )
            return

        ranking_text = "\n".join(
            f"**{i+1}.** {v['game_name']} ({v['rank']} pts)" for i, v in enumerate(votes)
        )
        attendance = db.get_attendance(cycle["id"], interaction.user.id)
        att_status = "Attending" if attendance else "Not attending"

        embed = discord.Embed(
            title="Your Current Vote",
            description=ranking_text,
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Attendance: {att_status} | Cycle #{cycle['id']}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voting(bot))
