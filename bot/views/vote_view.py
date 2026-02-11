"""Interactive button-based voting UI for stack ranking games."""

import discord

from bot import database as db


class VoteButton(discord.ui.Button["VoteView"]):
    """A single game button in the voting flow."""

    def __init__(self, game_id: int, game_name: str, row: int = 0):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=game_name,
            custom_id=f"vote_game_{game_id}",
            row=row,
        )
        self.game_id = game_id
        self.game_name = game_name

    async def callback(self, interaction: discord.Interaction) -> None:
        view: VoteView = self.view
        view.rankings.append((self.game_id, self.game_name))
        view.remaining = [(gid, gn) for gid, gn in view.remaining if gid != self.game_id]

        if not view.remaining:
            # All games ranked — show confirmation
            await interaction.response.edit_message(
                embed=view.build_confirmation_embed(), view=ConfirmVoteView(view)
            )
        else:
            # Show next pick
            next_view = VoteView(
                view.cycle_id,
                view.user_id,
                view.remaining,
                view.rankings,
            )
            rank_num = len(view.rankings) + 1
            await interaction.response.edit_message(
                embed=next_view.build_embed(rank_num), view=next_view
            )


class VoteView(discord.ui.View):
    """View that presents remaining games as buttons for the user to pick their next rank."""

    def __init__(
        self,
        cycle_id: int,
        user_id: int,
        remaining: list[tuple[int, str]],
        rankings: list[tuple[int, str]] | None = None,
    ):
        super().__init__(timeout=300)
        self.cycle_id = cycle_id
        self.user_id = user_id
        self.remaining = remaining
        self.rankings = rankings or []

        # Add buttons for remaining games (Discord limits: 5 buttons per row, 5 rows max)
        for i, (game_id, game_name) in enumerate(remaining):
            row = i // 5  # 5 buttons per row
            if row >= 4:  # Reserve last row for cancel button
                row = 3
            self.add_item(VoteButton(game_id, game_name, row=row))

        # Add cancel button on last row
        cancel_btn = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Cancel",
            custom_id="vote_cancel",
            row=4,
        )
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)

    def build_embed(self, rank_num: int) -> discord.Embed:
        embed = discord.Embed(
            title="MAVV Game Night Vote",
            color=discord.Color.blue(),
        )

        if self.rankings:
            ranking_text = "\n".join(
                f"**{i+1}.** {name}" for i, (_, name) in enumerate(self.rankings)
            )
            embed.add_field(name="Your ranking so far", value=ranking_text, inline=False)

        remaining_count = len(self.remaining)
        embed.add_field(
            name=f"Pick your #{rank_num} choice ({remaining_count} remaining)",
            value="Click a button below:",
            inline=False,
        )
        return embed

    def build_confirmation_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Confirm Your Vote",
            description="Here's your final ranking:",
            color=discord.Color.green(),
        )
        ranking_text = "\n".join(
            f"**{i+1}.** {name}" for i, (_, name) in enumerate(self.rankings)
        )
        embed.add_field(name="Your ranking", value=ranking_text, inline=False)
        embed.set_footer(text="Click Confirm to submit or Start Over to redo.")
        return embed

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Vote Cancelled",
                description="Your vote was not submitted. Use `/vote` to start again.",
                color=discord.Color.greyple(),
            ),
            view=None,
        )
        self.stop()

    async def on_timeout(self) -> None:
        pass  # Ephemeral messages can't be edited after timeout easily


class ConfirmVoteView(discord.ui.View):
    """Confirm or restart the vote."""

    def __init__(self, vote_view: VoteView):
        super().__init__(timeout=120)
        self.vote_view = vote_view

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Higher score = better. First pick gets N points, last pick gets 1.
        total = len(self.vote_view.rankings)
        rankings = [
            (game_id, total - pick_order)
            for pick_order, (game_id, _) in enumerate(self.vote_view.rankings)
        ]
        db.save_votes(self.vote_view.cycle_id, self.vote_view.user_id, rankings)

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Vote Submitted!",
                description="Your ranking has been recorded. You can use `/vote` again to change it before the deadline.",
                color=discord.Color.green(),
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Start Over", style=discord.ButtonStyle.secondary, row=0)
    async def start_over(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # Reset and start fresh
        all_games = self.vote_view.rankings + self.vote_view.remaining
        new_view = VoteView(
            self.vote_view.cycle_id,
            self.vote_view.user_id,
            all_games,
        )
        await interaction.response.edit_message(
            embed=new_view.build_embed(1), view=new_view
        )
        self.stop()


class AttendancePromptView(discord.ui.View):
    """Inline attendance prompt that flows into voting when the user picks 'Yes'."""

    def __init__(self, cycle_id: int):
        super().__init__(timeout=120)
        self.cycle_id = cycle_id

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title="MAVV Game Night — Are You Attending?",
            description="Before voting, let us know if you're playing this week!",
            color=discord.Color.blue(),
        )

    @discord.ui.button(label="Yes, I'm attending!", style=discord.ButtonStyle.success, row=0)
    async def attend_yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db.set_attendance(self.cycle_id, interaction.user.id, True)
        # Proceed directly to voting
        games = db.get_cycle_games(self.cycle_id)
        if not games:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Marked Attending!",
                    description="No games on the ballot yet. Use `/nominate` to add one, then `/vote` to rank.",
                    color=discord.Color.green(),
                ),
                view=None,
            )
            return

        remaining = [(g["game_id"], g["game_name"]) for g in games]
        view = VoteView(self.cycle_id, interaction.user.id, remaining)
        await interaction.response.edit_message(embed=view.build_embed(1), view=view)
        self.stop()

    @discord.ui.button(label="No, can't make it", style=discord.ButtonStyle.secondary, row=0)
    async def attend_no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db.set_attendance(self.cycle_id, interaction.user.id, False)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Marked Not Attending",
                description="No worries! You can change this anytime before results with `/attend yes`.",
                color=discord.Color.greyple(),
            ),
            view=None,
        )
        self.stop()


NOT_AUTHORIZED_MSG = (
    "You're not on the authorized voters list for MAVV Game Night. "
    "Ask an admin to add you with `/admin adduser`."
)


async def start_vote_flow(interaction: discord.Interaction, cycle: dict) -> None:
    """Unified entry point for voting. Prompts attendance if needed, then starts ranking."""
    if not db.is_authorized(interaction.user.id):
        await interaction.response.send_message(NOT_AUTHORIZED_MSG, ephemeral=True)
        return

    attendance = db.get_attendance(cycle["id"], interaction.user.id)

    if attendance is None:
        # Attendance not set — prompt first, then flow into voting
        view = AttendancePromptView(cycle["id"])
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        return

    if not attendance:
        # Marked not attending — offer to change
        view = AttendancePromptView(cycle["id"])
        embed = view.build_embed()
        embed.description = (
            "You're currently marked as **not attending**. "
            "Change your mind? Pick an option below."
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # Attending — go straight to voting
    games = db.get_cycle_games(cycle["id"])
    if not games:
        await interaction.response.send_message(
            "No games on the ballot yet. Use `/nominate` to add one!", ephemeral=True
        )
        return

    remaining = [(g["game_id"], g["game_name"]) for g in games]
    view = VoteView(cycle["id"], interaction.user.id, remaining)

    existing = db.get_user_votes(cycle["id"], interaction.user.id)
    embed = view.build_embed(1)
    if existing:
        embed.set_footer(text="You already have a vote on record. Completing this will replace it.")

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class VoteNowButton(discord.ui.View):
    """Persistent button on the cycle announcement for quick access to voting."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Vote Now",
        style=discord.ButtonStyle.success,
        custom_id="persistent_vote_now",
        emoji="\U0001f5f3\ufe0f",
    )
    async def vote_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "No active voting cycle right now.", ephemeral=True
            )
            return

        if cycle["status"] == "runoff":
            await interaction.response.send_message(
                "Voting has moved to a **runoff**! Look for the "
                "**Runoff Vote** message in this channel to cast your tie-breaker vote.",
                ephemeral=True,
            )
            return

        if cycle["status"] != "open":
            await interaction.response.send_message(
                "Voting is not currently open.", ephemeral=True
            )
            return

        await start_vote_flow(interaction, dict(cycle))

    @discord.ui.button(
        label="Not Attending",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent_attend_no",
        emoji="\u274c",
    )
    async def attend_no(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not db.is_authorized(interaction.user.id):
            await interaction.response.send_message(NOT_AUTHORIZED_MSG, ephemeral=True)
            return

        cycle = db.get_current_cycle()
        if not cycle:
            await interaction.response.send_message(
                "No active voting cycle right now.", ephemeral=True
            )
            return
        db.set_attendance(cycle["id"], interaction.user.id, False)
        await interaction.response.send_message(
            "You're marked as **not attending** this week. You can change this anytime before results with `/attend yes`.",
            ephemeral=True,
        )
