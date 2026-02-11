# MAVV Demobot 2.9

Discord bot for managing weekly MAVV Game Night voting. Handles nominations, attendance, stack-ranked voting, tie-breaking runoffs, and automated scheduling.

## Features

- **Automated weekly cycle** — Opens voting, sends reminders, publishes results on schedule
- **Interactive button-based voting** — Stack rank games by clicking buttons (works on desktop and mobile)
- **Integrated attendance** — Voting flow prompts attendance first, changeable anytime before results
- **Nominations** — Each member gets 1 nomination per week (configurable), up to 10 games total
- **Carry-over** — Top 5 games from last week auto-populate the next ballot
- **Tie-breaking runoffs** — Single-pick vote among tied games with configurable duration
- **Anonymous voting** — Individual votes are never revealed, only aggregate scores
- **Reminder DMs** — Auto-reminds attending members who haven't voted yet
- **Higher = better scoring** — First choice gets max points, averaged across attending voters

## Quick Start

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**, name it "MAVV Demobot"
3. Go to **Bot** → Click **Reset Token** → Copy the token
4. Under **Privileged Gateway Intents**, enable:
   - **Server Members Intent**
   - **Message Content Intent**
5. Go to **OAuth2** → **URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`, `Read Message History`
6. Copy the generated URL and open it in your browser to invite the bot to your server

### 2. Get Your Discord IDs

Enable Developer Mode in Discord: User Settings → Advanced → Developer Mode

- **Server ID**: Right-click your server name → Copy Server ID
- **Channel ID**: Right-click your voting channel → Copy Channel ID
- **User IDs**: Right-click your username → Copy User ID (for admin list)

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### 4. Run with Docker Compose

```bash
docker compose up -d
```

The bot will start, sync slash commands to your server, and begin the automated schedule.

### 5. First-Time Setup

1. Run `/admin start` to open the first voting cycle
2. Run `/admin seed Game1, Game2, Game3, Game4, Game5` to add initial games
3. Members can now click **Vote Now** on the announcement or use `/vote`

After the first week, the bot handles everything automatically.

## Unraid Setup

### Option A: Docker Compose (Recommended)

1. Clone this repo to your Unraid server
2. Copy `.env.example` to `.env` and fill in your values
3. Run `docker compose up -d`

### Option B: Unraid Template

1. Build the image: `docker build -t mavv-demobot .`
2. In Unraid, go to **Docker** → **Add Container**
3. Use the template from `unraid/mavv-demobot.xml` or manually configure:
   - **Repository**: `mavv-demobot`
   - **Volume**: `/mnt/user/appdata/mavv-demobot/data` → `/app/data`
   - Add all environment variables from `.env.example`

## Commands

### Everyone

| Command | Description |
|---------|-------------|
| `/vote` | Start the interactive voting flow (prompts attendance if needed) |
| `/attend yes/no` | Set or change your attendance |
| `/nominate <game>` | Nominate a game for this week |
| `/myvote` | See your current submitted ranking |
| `/status` | See current cycle status (games, votes, attendance) |
| `/results` | View the latest published results |

### Admin Only

| Command | Description |
|---------|-------------|
| `/admin start` | Manually start a new voting cycle |
| `/admin close` | Manually close voting and publish results |
| `/admin addgame <name>` | Add a game to the current ballot |
| `/admin removegame <name>` | Remove a game from the ballot |
| `/admin mergegame <from> <into>` | Merge duplicate game names |
| `/admin seed <game1, game2, ...>` | Seed initial games (comma-separated) |
| `/admin reminder` | Manually send reminder DMs to non-voters |

## How Voting Works

1. **Cycle opens** (Tuesday 9 AM PT by default) — Bot posts announcement with carry-over games
2. **Members nominate** — Use `/nominate` to add games (1 per person, up to 10 total)
3. **Members vote** — Click **Vote Now** or `/vote`:
   - If attendance not set, prompts "Are you attending?" first
   - Then pick games in preference order (#1 favorite first)
   - First pick = highest score (N points), last pick = 1 point
4. **Reminders** (Thursday 6 PM PT) — Bot DMs attending members who haven't voted
5. **Results** (Friday 9 AM PT) — Bot calculates average scores, posts results
   - If tied: runoff poll with single-pick among tied games (2 hours)
6. **Carry-over** — Top 5 games populate next week's ballot

## Environment Variables

See `.env.example` for the full list with descriptions. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | (required) | Bot token |
| `DISCORD_GUILD_ID` | (required) | Server ID |
| `VOTE_CHANNEL_ID` | (required) | Voting channel ID |
| `ADMIN_USER_IDS` | (required) | Comma-separated admin user IDs |
| `VOTE_OPEN_DAY` / `VOTE_OPEN_TIME` | tuesday / 09:00 | When voting opens |
| `RESULTS_DAY` / `RESULTS_TIME` | friday / 09:00 | When results publish |
| `REMINDER_DAY` / `REMINDER_TIME` | thursday / 18:00 | When reminders go out |
| `TIMEZONE` | America/Los_Angeles | IANA timezone for all times |
| `RUNOFF_DURATION_MINUTES` | 120 | How long runoffs last |
| `MAX_TOTAL_GAMES` | 10 | Max games on ballot |
| `CARRY_OVER_COUNT` | 5 | Games carried to next week |

## Project Structure

```
├── bot/
│   ├── main.py          # Bot entry point
│   ├── config.py         # Environment variable loading
│   ├── database.py       # SQLite schema and queries
│   ├── cogs/
│   │   ├── voting.py     # /vote, /attend, /nominate, /myvote
│   │   ├── admin.py      # /admin commands
│   │   ├── results.py    # /results, /status, result publishing
│   │   └── scheduler.py  # APScheduler automated cycle
│   └── views/
│       ├── vote_view.py  # Interactive button ranking UI
│       └── runoff_view.py # Runoff single-pick UI
├── data/                  # SQLite database (Docker volume)
├── unraid/
│   └── mavv-demobot.xml  # Unraid container template
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## License

MIT
