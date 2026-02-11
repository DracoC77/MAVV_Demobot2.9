"""Runoff voting view — single pick among tied games."""

import discord

from bot import database as db


class RunoffButton(discord.ui.Button["RunoffView"]):
    """Button for a single game in the runoff."""

    def __init__(self, game_id: int, game_name: str, row: int = 0):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=game_name,
            custom_id=f"runoff_game_{game_id}",
            row=row,
        )
        self.game_id = game_id
        self.game_name = game_name

    async def callback(self, interaction: discord.Interaction) -> None:
        view: RunoffView = self.view
        cycle = db.get_current_cycle()
        if not cycle or cycle["status"] != "runoff":
            await interaction.response.send_message(
                "The runoff has ended.", ephemeral=True
            )
            return

        # Check attendance
        attendance = db.get_attendance(cycle["id"], interaction.user.id)
        if not attendance:
            await interaction.response.send_message(
                "Only attending members can vote in the runoff.", ephemeral=True
            )
            return

        db.save_runoff_vote(
            cycle["id"], interaction.user.id, self.game_id, view.message_id
        )
        await interaction.response.send_message(
            f"Your runoff vote for **{self.game_name}** has been recorded! "
            f"You can click again to change your pick before the runoff ends.",
            ephemeral=True,
        )


class RunoffView(discord.ui.View):
    """Persistent view for runoff voting."""

    def __init__(self, cycle_id: int, tied_games: list[tuple[int, str]], message_id: int = 0):
        super().__init__(timeout=None)
        self.cycle_id = cycle_id
        self.tied_games = tied_games
        self.message_id = message_id

        for i, (game_id, game_name) in enumerate(tied_games):
            row = i // 5
            self.add_item(RunoffButton(game_id, game_name, row=row))

    def build_embed(self) -> discord.Embed:
        game_list = "\n".join(f"- **{name}**" for _, name in self.tied_games)
        embed = discord.Embed(
            title="Runoff Vote Required!",
            description=(
                "The following games are tied! Attending members: pick **one** game.\n\n"
                f"{game_list}\n\n"
                "Click a button below to cast your runoff vote."
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="You can change your pick by clicking a different button.")
        return embed
