"""Admin cog — management commands restricted to configured admin users."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import database as db
from bot.views.vote_view import VoteNowButton

log = logging.getLogger("demobot.admin")


def is_admin():
    """Check decorator: only configured admin user IDs can use the command."""

    async def predicate(interaction: discord.Interaction) -> bool:
        config = interaction.client.config
        if interaction.user.id not in config.admin_user_ids:
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return False
        return True

    return app_commands.check(predicate)


class Admin(commands.Cog):
    admin_group = app_commands.Group(
        name="admin", description="Admin commands for managing MAVV Demobot"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @admin_group.command(name="start", description="Manually start a new voting cycle")
    @is_admin()
    async def start_cycle(self, interaction: discord.Interaction) -> None:
        existing = db.get_current_cycle()
        if existing:
            await interaction.response.send_message(
                f"There's already an active cycle (#{existing['id']}). "
                "Close it first with `/admin close`.",
                ephemeral=True,
            )
            return

        cycle_id = db.create_cycle()

        # Auto carry-over from last published cycle
        config = self.bot.config
        latest = db.get_latest_cycle()
        # The latest cycle before the one we just created
        conn = db.get_connection()
        prev = conn.execute(
            "SELECT * FROM voting_cycles WHERE id < ? AND status = 'published' ORDER BY id DESC LIMIT 1",
            (cycle_id,),
        ).fetchone()
        conn.close()

        carried = 0
        if prev:
            top_games = db.get_top_games_from_cycle(prev["id"], config.carry_over_count)
            for game in top_games:
                db.add_game_to_cycle(cycle_id, game["game_id"], is_carry_over=True)
                carried += 1

        # Post announcement
        channel = self.bot.get_channel(config.vote_channel_id)
        games = db.get_cycle_games(cycle_id)
        embed = build_cycle_announcement(cycle_id, games, config)

        view = VoteNowButton()
        if channel:
            msg = await channel.send(embed=embed, view=view)
            db.set_cycle_announcement_message(cycle_id, msg.id)

        await interaction.response.send_message(
            f"Voting cycle #{cycle_id} started! {carried} games carried over. "
            f"Announcement posted in <#{config.vote_channel_id}>.",
            ephemeral=True,
        )

    @admin_group.command(
        name="close", description="Manually close voting and publish results"
    )
    @is_admin()
    async def close_cycle(self, interaction: discord.Interaction) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "No active voting cycle to close.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        from bot.cogs.results import publish_results

        await publish_results(self.bot, cycle["id"])

        await interaction.followup.send(
            f"Cycle #{cycle['id']} has been closed and results published.",
            ephemeral=True,
        )

    @admin_group.command(name="addgame", description="Add a game to the current ballot")
    @app_commands.describe(name="Name of the game to add")
    @is_admin()
    async def add_game(self, interaction: discord.Interaction, name: str) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "No active voting cycle.", ephemeral=True
            )
            return

        game_name = name.strip()
        game_id = db.get_or_create_game(game_name, added_by=interaction.user.id)
        added = db.add_game_to_cycle(cycle["id"], game_id, is_carry_over=False)

        if not added:
            await interaction.response.send_message(
                f"**{game_name}** is already on the ballot.", ephemeral=True
            )
            return

        channel = self.bot.get_channel(self.bot.config.vote_channel_id)
        if channel:
            await channel.send(f"Admin added **{game_name}** to this week's ballot.")

        await interaction.response.send_message(
            f"**{game_name}** added to cycle #{cycle['id']}.", ephemeral=True
        )

    @admin_group.command(
        name="removegame", description="Remove a game from the current ballot"
    )
    @app_commands.describe(name="Name of the game to remove")
    @is_admin()
    async def remove_game(self, interaction: discord.Interaction, name: str) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "No active voting cycle.", ephemeral=True
            )
            return

        conn = db.get_connection()
        row = conn.execute(
            "SELECT id FROM games WHERE name = ? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
        conn.close()

        if not row:
            await interaction.response.send_message(
                f"Game **{name}** not found.", ephemeral=True
            )
            return

        removed = db.remove_game_from_cycle(cycle["id"], row["id"])
        if removed:
            await interaction.response.send_message(
                f"**{name}** removed from the ballot.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"**{name}** was not on this week's ballot.", ephemeral=True
            )

    @admin_group.command(
        name="mergegame",
        description="Merge one game name into another (fixes duplicates)",
    )
    @app_commands.describe(
        from_name="The duplicate game name to merge away",
        into_name="The canonical game name to keep",
    )
    @is_admin()
    async def merge_game(
        self, interaction: discord.Interaction, from_name: str, into_name: str
    ) -> None:
        success = db.merge_games(from_name.strip(), into_name.strip())
        if success:
            await interaction.response.send_message(
                f"Merged **{from_name}** into **{into_name}**. All votes and history updated.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "One or both game names not found.", ephemeral=True
            )

    @admin_group.command(
        name="seed",
        description="Seed initial games for the current cycle (comma-separated)",
    )
    @app_commands.describe(games="Comma-separated list of game names")
    @is_admin()
    async def seed_games(self, interaction: discord.Interaction, games: str) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "No active voting cycle. Start one with `/admin start`.",
                ephemeral=True,
            )
            return

        game_names = [g.strip() for g in games.split(",") if g.strip()]
        if not game_names:
            await interaction.response.send_message(
                "Provide at least one game name.", ephemeral=True
            )
            return

        added = []
        for name in game_names:
            game_id = db.get_or_create_game(name, added_by=interaction.user.id)
            if db.add_game_to_cycle(cycle["id"], game_id, is_carry_over=True):
                added.append(name)

        if added:
            game_list = ", ".join(f"**{n}**" for n in added)
            channel = self.bot.get_channel(self.bot.config.vote_channel_id)
            if channel:
                await channel.send(f"Games seeded for this cycle: {game_list}")
            await interaction.response.send_message(
                f"Seeded {len(added)} games: {game_list}", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "All those games were already on the ballot.", ephemeral=True
            )

    @admin_group.command(
        name="adduser", description="Add a user to the authorized voters list"
    )
    @app_commands.describe(user="The Discord user to authorize")
    @is_admin()
    async def add_user(self, interaction: discord.Interaction, user: discord.User) -> None:
        added = db.add_authorized_user(user.id, interaction.user.id, user.display_name)
        if added:
            await interaction.response.send_message(
                f"**{user.display_name}** has been added to the authorized voters list.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"**{user.display_name}** is already authorized (display name updated).",
                ephemeral=True,
            )

    @admin_group.command(
        name="removeuser", description="Remove a user from the authorized voters list"
    )
    @app_commands.describe(user="The Discord user to remove")
    @is_admin()
    async def remove_user(self, interaction: discord.Interaction, user: discord.User) -> None:
        removed = db.remove_authorized_user(user.id)
        if removed:
            await interaction.response.send_message(
                f"**{user.display_name}** has been removed from the authorized voters list.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"**{user.display_name}** was not on the authorized list.",
                ephemeral=True,
            )

    @admin_group.command(
        name="users", description="List all authorized voters"
    )
    @is_admin()
    async def list_users(self, interaction: discord.Interaction) -> None:
        users = db.get_authorized_users()
        if not users:
            await interaction.response.send_message(
                "No authorized users yet. Use `/admin adduser` to add members.",
                ephemeral=True,
            )
            return

        lines = []
        for u in users:
            name = u["display_name"] or f"User {u['user_id']}"
            lines.append(f"- **{name}** (<@{u['user_id']}>)")

        embed = discord.Embed(
            title="Authorized Voters",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{len(users)} authorized members")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @admin_group.command(
        name="reminder", description="Manually send reminder DMs to non-voters"
    )
    @is_admin()
    async def send_reminder(self, interaction: discord.Interaction) -> None:
        cycle = db.get_current_cycle()
        if not cycle or cycle["status"] != "open":
            await interaction.response.send_message(
                "No open voting cycle.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        attending = db.get_attending_users(cycle["id"])
        voters = db.get_voters(cycle["id"])
        non_voters = [uid for uid in attending if uid not in voters]

        sent = 0
        failed = 0
        for uid in non_voters:
            try:
                user = await self.bot.fetch_user(uid)
                await user.send(
                    f"Reminder: You haven't submitted your game night vote yet! "
                    f"Head to <#{self.bot.config.vote_channel_id}> and use `/vote` "
                    f"or click **Vote Now** to rank this week's games."
                )
                sent += 1
            except Exception:
                failed += 1

        await interaction.followup.send(
            f"Reminders sent to {sent} members. {failed} failed.", ephemeral=True
        )


def build_cycle_announcement(
    cycle_id: int, games: list, config
) -> discord.Embed:
    """Build the embed for a new cycle announcement."""
    embed = discord.Embed(
        title="MAVV Game Night — New Voting Cycle!",
        description=(
            "A new week of voting has begun! Set your attendance and rank the games.\n\n"
            "**How it works:**\n"
            "1. Click **Set Attending** or use `/attend yes`\n"
            "2. Use `/nominate <game>` to add a game (1 per person)\n"
            "3. Click **Vote Now** or use `/vote` to rank the games\n"
            "4. Results will be published automatically!"
        ),
        color=discord.Color.gold(),
    )

    if games:
        game_list = "\n".join(
            f"{'(carry-over) ' if g['is_carry_over'] else ''}{g['game_name']}"
            for g in games
        )
        embed.add_field(name="Games on the Ballot", value=game_list, inline=False)
    else:
        embed.add_field(
            name="Games on the Ballot",
            value="No games yet — use `/nominate` or `/admin seed` to add games!",
            inline=False,
        )

    nom_slots = config.max_total_games - len(games)
    embed.add_field(
        name="Nomination Slots",
        value=f"{nom_slots} of {config.max_total_games} remaining",
        inline=True,
    )
    embed.set_footer(text=f"Cycle #{cycle_id}")
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
