"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


@dataclass
class Config:
    # Discord
    discord_token: str = ""
    guild_id: int = 0
    vote_channel_id: int = 0
    admin_user_ids: list[int] = field(default_factory=list)

    # Schedule (day of week + HH:MM in configured timezone)
    vote_open_day: str = "tuesday"
    vote_open_time: str = "09:00"
    results_day: str = "friday"
    results_time: str = "09:00"
    reminder_day: str = "thursday"
    reminder_time: str = "18:00"
    attendance_cutoff_day: str = "friday"
    attendance_cutoff_time: str = "09:00"

    # Runoff
    runoff_duration_minutes: int = 120

    # Timezone
    timezone: str = "America/Los_Angeles"

    # Game settings
    max_nominations_per_person: int = 1
    max_total_games: int = 10
    carry_over_count: int = 5

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def from_env(cls) -> "Config":
        admin_ids_raw = os.environ.get("ADMIN_USER_IDS", "")
        admin_ids = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]

        return cls(
            discord_token=os.environ["DISCORD_TOKEN"],
            guild_id=int(os.environ.get("DISCORD_GUILD_ID", 0)),
            vote_channel_id=int(os.environ.get("VOTE_CHANNEL_ID", 0)),
            admin_user_ids=admin_ids,
            vote_open_day=os.environ.get("VOTE_OPEN_DAY", "tuesday").lower(),
            vote_open_time=os.environ.get("VOTE_OPEN_TIME", "09:00"),
            results_day=os.environ.get("RESULTS_DAY", "friday").lower(),
            results_time=os.environ.get("RESULTS_TIME", "09:00"),
            reminder_day=os.environ.get("REMINDER_DAY", "thursday").lower(),
            reminder_time=os.environ.get("REMINDER_TIME", "18:00"),
            attendance_cutoff_day=os.environ.get("ATTENDANCE_CUTOFF_DAY", "friday").lower(),
            attendance_cutoff_time=os.environ.get("ATTENDANCE_CUTOFF_TIME", "09:00"),
            runoff_duration_minutes=int(os.environ.get("RUNOFF_DURATION_MINUTES", 120)),
            timezone=os.environ.get("TIMEZONE", "America/Los_Angeles"),
            max_nominations_per_person=int(os.environ.get("MAX_NOMINATIONS_PER_PERSON", 1)),
            max_total_games=int(os.environ.get("MAX_TOTAL_GAMES", 10)),
            carry_over_count=int(os.environ.get("CARRY_OVER_COUNT", 5)),
        )
