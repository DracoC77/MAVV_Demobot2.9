"""SQLite database layer for MAVV Demobot."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path("/app/data/demobot.db")


def get_db_path() -> Path:
    """Return the database path, allowing override for local dev."""
    import os

    override = os.environ.get("DB_PATH")
    if override:
        return Path(override)
    return DB_PATH


def get_connection() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't exist."""
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            added_by INTEGER,
            added_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS voting_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'open',
            opened_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT,
            results_published_at TEXT,
            winning_game_id INTEGER REFERENCES games(id),
            announcement_message_id INTEGER,
            runoff_round INTEGER NOT NULL DEFAULT 0,
            runoff_deadline TEXT
        );

        CREATE TABLE IF NOT EXISTS cycle_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL REFERENCES voting_cycles(id),
            game_id INTEGER NOT NULL REFERENCES games(id),
            is_carry_over INTEGER NOT NULL DEFAULT 0,
            nominated_by INTEGER,
            UNIQUE(cycle_id, game_id)
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL REFERENCES voting_cycles(id),
            user_id INTEGER NOT NULL,
            attending INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(cycle_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL REFERENCES voting_cycles(id),
            user_id INTEGER NOT NULL,
            game_id INTEGER NOT NULL REFERENCES games(id),
            rank INTEGER NOT NULL,
            voted_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(cycle_id, user_id, game_id)
        );

        CREATE TABLE IF NOT EXISTS runoff_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL REFERENCES voting_cycles(id),
            user_id INTEGER NOT NULL,
            game_id INTEGER NOT NULL REFERENCES games(id),
            voted_at TEXT NOT NULL DEFAULT (datetime('now')),
            runoff_message_id INTEGER,
            UNIQUE(cycle_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS authorized_users (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            display_name TEXT
        );

        CREATE TABLE IF NOT EXISTS cycle_runoff_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL REFERENCES voting_cycles(id),
            game_id INTEGER NOT NULL REFERENCES games(id),
            UNIQUE(cycle_id, game_id)
        );

        CREATE TABLE IF NOT EXISTS pending_nominations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL REFERENCES games(id),
            nominated_by INTEGER NOT NULL,
            nominated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id)
        );
    """
    )
    conn.commit()

    # Migrations for existing databases
    for col, default in [("runoff_round", "0"), ("runoff_deadline", "NULL")]:
        try:
            if default == "NULL":
                conn.execute(f"ALTER TABLE voting_cycles ADD COLUMN {col} TEXT")
            else:
                conn.execute(
                    f"ALTER TABLE voting_cycles ADD COLUMN {col} INTEGER NOT NULL DEFAULT {default}"
                )
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.close()


# ---------------------------------------------------------------------------
# Authorized Users helpers
# ---------------------------------------------------------------------------


def add_authorized_user(user_id: int, added_by: int, display_name: Optional[str] = None) -> bool:
    """Add a user to the authorized voters list. Returns False if already authorized."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO authorized_users (user_id, added_by, display_name) VALUES (?, ?, ?)",
            (user_id, added_by, display_name),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Update display name if it changed
        conn.execute(
            "UPDATE authorized_users SET display_name = ? WHERE user_id = ?",
            (display_name, user_id),
        )
        conn.commit()
        return False
    finally:
        conn.close()


def remove_authorized_user(user_id: int) -> bool:
    """Remove a user from the authorized voters list."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM authorized_users WHERE user_id = ?", (user_id,))
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


def is_authorized(user_id: int) -> bool:
    """Check if a user is on the authorized voters list."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM authorized_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row is not None


def get_authorized_users() -> list[sqlite3.Row]:
    """Get all authorized users."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM authorized_users ORDER BY display_name"
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Voting Cycle helpers
# ---------------------------------------------------------------------------


def create_cycle() -> int:
    """Create a new voting cycle and return its ID."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO voting_cycles (status, opened_at) VALUES ('open', datetime('now'))"
    )
    cycle_id = cur.lastrowid
    conn.commit()
    conn.close()
    return cycle_id


def get_current_cycle() -> Optional[sqlite3.Row]:
    """Get the current open or runoff cycle."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM voting_cycles WHERE status IN ('open', 'runoff') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def get_latest_cycle() -> Optional[sqlite3.Row]:
    """Get the most recent cycle regardless of status."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM voting_cycles ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def close_cycle(cycle_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE voting_cycles SET status = 'closed', closed_at = datetime('now') WHERE id = ?",
        (cycle_id,),
    )
    conn.commit()
    conn.close()


def set_cycle_runoff(cycle_id: int) -> int:
    """Set cycle to runoff status and increment the round counter. Returns new round number."""
    conn = get_connection()
    conn.execute(
        "UPDATE voting_cycles SET status = 'runoff', runoff_round = runoff_round + 1 WHERE id = ?",
        (cycle_id,),
    )
    row = conn.execute(
        "SELECT runoff_round FROM voting_cycles WHERE id = ?", (cycle_id,)
    ).fetchone()
    conn.commit()
    conn.close()
    return row["runoff_round"]


def publish_cycle(cycle_id: int, winning_game_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE voting_cycles SET status = 'published', results_published_at = datetime('now'), "
        "winning_game_id = ? WHERE id = ?",
        (winning_game_id, cycle_id),
    )
    conn.commit()
    conn.close()


def set_cycle_announcement_message(cycle_id: int, message_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE voting_cycles SET announcement_message_id = ? WHERE id = ?",
        (message_id, cycle_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Games helpers
# ---------------------------------------------------------------------------


def get_or_create_game(name: str, added_by: Optional[int] = None) -> int:
    """Return the game ID, creating the game if it doesn't exist."""
    conn = get_connection()
    row = conn.execute("SELECT id FROM games WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        game_id = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO games (name, added_by) VALUES (?, ?)", (name, added_by)
        )
        game_id = cur.lastrowid
    conn.commit()
    conn.close()
    return game_id


def get_game_by_id(game_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    conn.close()
    return row


def rename_game(old_name: str, new_name: str) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE games SET name = ? WHERE name = ? COLLATE NOCASE", (new_name, old_name)
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def merge_games(from_name: str, into_name: str) -> bool:
    """Merge game 'from_name' into 'into_name', updating all references."""
    conn = get_connection()
    from_row = conn.execute(
        "SELECT id FROM games WHERE name = ? COLLATE NOCASE", (from_name,)
    ).fetchone()
    into_row = conn.execute(
        "SELECT id FROM games WHERE name = ? COLLATE NOCASE", (into_name,)
    ).fetchone()
    if not from_row or not into_row:
        conn.close()
        return False

    from_id, into_id = from_row["id"], into_row["id"]

    # Update votes
    conn.execute("UPDATE OR IGNORE votes SET game_id = ? WHERE game_id = ?", (into_id, from_id))
    conn.execute("DELETE FROM votes WHERE game_id = ?", (from_id,))

    # Update cycle_games
    conn.execute(
        "UPDATE OR IGNORE cycle_games SET game_id = ? WHERE game_id = ?", (into_id, from_id)
    )
    conn.execute("DELETE FROM cycle_games WHERE game_id = ?", (from_id,))

    # Update runoff_votes
    conn.execute(
        "UPDATE OR IGNORE runoff_votes SET game_id = ? WHERE game_id = ?", (into_id, from_id)
    )
    conn.execute("DELETE FROM runoff_votes WHERE game_id = ?", (from_id,))

    # Update pending_nominations
    conn.execute(
        "UPDATE OR IGNORE pending_nominations SET game_id = ? WHERE game_id = ?",
        (into_id, from_id),
    )
    conn.execute("DELETE FROM pending_nominations WHERE game_id = ?", (from_id,))

    # Update winning references
    conn.execute(
        "UPDATE voting_cycles SET winning_game_id = ? WHERE winning_game_id = ?",
        (into_id, from_id),
    )

    # Delete the old game
    conn.execute("DELETE FROM games WHERE id = ?", (from_id,))
    conn.commit()
    conn.close()
    return True


# ---------------------------------------------------------------------------
# Cycle-Games helpers
# ---------------------------------------------------------------------------


def add_game_to_cycle(
    cycle_id: int, game_id: int, is_carry_over: bool = False, nominated_by: Optional[int] = None
) -> bool:
    """Add a game to a cycle. Returns False if already exists."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO cycle_games (cycle_id, game_id, is_carry_over, nominated_by) "
            "VALUES (?, ?, ?, ?)",
            (cycle_id, game_id, int(is_carry_over), nominated_by),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def remove_game_from_cycle(cycle_id: int, game_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM cycle_games WHERE cycle_id = ? AND game_id = ?", (cycle_id, game_id)
    )
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


def get_cycle_games(cycle_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT cg.*, g.name AS game_name FROM cycle_games cg "
        "JOIN games g ON g.id = cg.game_id WHERE cg.cycle_id = ? ORDER BY g.name",
        (cycle_id,),
    ).fetchall()
    conn.close()
    return rows


def get_cycle_game_count(cycle_id: int) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM cycle_games WHERE cycle_id = ?", (cycle_id,)
    ).fetchone()
    conn.close()
    return row["cnt"]


def get_user_nomination_count(cycle_id: int, user_id: int) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM cycle_games WHERE cycle_id = ? AND nominated_by = ? AND is_carry_over = 0",
        (cycle_id, user_id),
    ).fetchone()
    conn.close()
    return row["cnt"]


# ---------------------------------------------------------------------------
# Attendance helpers
# ---------------------------------------------------------------------------


def set_attendance(cycle_id: int, user_id: int, attending: bool) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO attendance (cycle_id, user_id, attending, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(cycle_id, user_id) DO UPDATE SET attending = excluded.attending, "
        "updated_at = excluded.updated_at",
        (cycle_id, user_id, int(attending)),
    )
    conn.commit()
    conn.close()


def get_attendance(cycle_id: int, user_id: int) -> Optional[bool]:
    conn = get_connection()
    row = conn.execute(
        "SELECT attending FROM attendance WHERE cycle_id = ? AND user_id = ?",
        (cycle_id, user_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return bool(row["attending"])


def get_attending_users(cycle_id: int) -> list[int]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT user_id FROM attendance WHERE cycle_id = ? AND attending = 1",
        (cycle_id,),
    ).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def get_all_attendance(cycle_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM attendance WHERE cycle_id = ?", (cycle_id,)
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Vote helpers
# ---------------------------------------------------------------------------


def save_votes(cycle_id: int, user_id: int, rankings: list[tuple[int, int]]) -> None:
    """Save a user's full ranking. rankings = [(game_id, score), ...] where higher = better."""
    conn = get_connection()
    # Clear previous votes for this user/cycle
    conn.execute(
        "DELETE FROM votes WHERE cycle_id = ? AND user_id = ?", (cycle_id, user_id)
    )
    for game_id, rank in rankings:
        conn.execute(
            "INSERT INTO votes (cycle_id, user_id, game_id, rank, voted_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (cycle_id, user_id, game_id, rank),
        )
    conn.commit()
    conn.close()


def get_user_votes(cycle_id: int, user_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT v.*, g.name AS game_name FROM votes v "
        "JOIN games g ON g.id = v.game_id "
        "WHERE v.cycle_id = ? AND v.user_id = ? ORDER BY v.rank DESC",
        (cycle_id, user_id),
    ).fetchall()
    conn.close()
    return rows


def get_voters(cycle_id: int) -> list[int]:
    """Get all user IDs that have submitted votes for a cycle."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM votes WHERE cycle_id = ?", (cycle_id,)
    ).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def calculate_results(cycle_id: int) -> list[dict]:
    """Calculate average scores for attending voters. Higher = better. Returns sorted list (best first)."""
    conn = get_connection()
    attending = get_attending_users(cycle_id)
    if not attending:
        conn.close()
        return []

    placeholders = ",".join("?" * len(attending))
    rows = conn.execute(
        f"SELECT v.game_id, g.name AS game_name, AVG(v.rank) AS avg_score, COUNT(v.user_id) AS vote_count "
        f"FROM votes v "
        f"JOIN games g ON g.id = v.game_id "
        f"WHERE v.cycle_id = ? AND v.user_id IN ({placeholders}) "
        f"GROUP BY v.game_id "
        f"ORDER BY avg_score DESC",
        (cycle_id, *attending),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Runoff helpers
# ---------------------------------------------------------------------------


def save_runoff_vote(cycle_id: int, user_id: int, game_id: int, message_id: int = 0) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO runoff_votes (cycle_id, user_id, game_id, voted_at, runoff_message_id) "
        "VALUES (?, ?, ?, datetime('now'), ?) "
        "ON CONFLICT(cycle_id, user_id) DO UPDATE SET game_id = excluded.game_id, "
        "voted_at = excluded.voted_at",
        (cycle_id, user_id, game_id, message_id),
    )
    conn.commit()
    conn.close()


def get_runoff_voters(cycle_id: int) -> list[int]:
    """Get all user IDs that have cast a runoff vote for a cycle."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM runoff_votes WHERE cycle_id = ?", (cycle_id,)
    ).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def get_runoff_results(cycle_id: int) -> list[dict]:
    """Count runoff votes per game among attending users."""
    attending = get_attending_users(cycle_id)
    if not attending:
        return []
    conn = get_connection()
    placeholders = ",".join("?" * len(attending))
    rows = conn.execute(
        f"SELECT rv.game_id, g.name AS game_name, COUNT(*) AS vote_count "
        f"FROM runoff_votes rv "
        f"JOIN games g ON g.id = rv.game_id "
        f"WHERE rv.cycle_id = ? AND rv.user_id IN ({placeholders}) "
        f"GROUP BY rv.game_id ORDER BY vote_count DESC",
        (cycle_id, *attending),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_runoff_votes(cycle_id: int) -> None:
    """Clear all runoff votes for a cycle (used when starting a re-runoff)."""
    conn = get_connection()
    conn.execute("DELETE FROM runoff_votes WHERE cycle_id = ?", (cycle_id,))
    conn.commit()
    conn.close()


def get_runoff_round(cycle_id: int) -> int:
    """Get the current runoff round number for a cycle (0 = no runoff yet)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT runoff_round FROM voting_cycles WHERE id = ?", (cycle_id,)
    ).fetchone()
    conn.close()
    return row["runoff_round"] if row else 0


def set_runoff_games(cycle_id: int, game_ids: list[int]) -> None:
    """Store which games are in the current runoff."""
    conn = get_connection()
    conn.execute("DELETE FROM cycle_runoff_games WHERE cycle_id = ?", (cycle_id,))
    for gid in game_ids:
        conn.execute(
            "INSERT INTO cycle_runoff_games (cycle_id, game_id) VALUES (?, ?)",
            (cycle_id, gid),
        )
    conn.commit()
    conn.close()


def get_runoff_games(cycle_id: int) -> list[dict]:
    """Get the games in the current runoff."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT crg.game_id, g.name AS game_name FROM cycle_runoff_games crg "
        "JOIN games g ON g.id = crg.game_id WHERE crg.cycle_id = ? ORDER BY g.name",
        (cycle_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_runoff_deadline(cycle_id: int, deadline_iso: str) -> None:
    """Store the runoff deadline as an ISO datetime string."""
    conn = get_connection()
    conn.execute(
        "UPDATE voting_cycles SET runoff_deadline = ? WHERE id = ?",
        (deadline_iso, cycle_id),
    )
    conn.commit()
    conn.close()


def get_user_runoff_vote(cycle_id: int, user_id: int) -> Optional[dict]:
    """Get a user's runoff vote for a cycle."""
    conn = get_connection()
    row = conn.execute(
        "SELECT rv.game_id, g.name AS game_name FROM runoff_votes rv "
        "JOIN games g ON g.id = rv.game_id "
        "WHERE rv.cycle_id = ? AND rv.user_id = ?",
        (cycle_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Pending Nominations helpers
# ---------------------------------------------------------------------------


def add_pending_nomination(game_id: int, nominated_by: int) -> bool:
    """Add a game to the pending nominations pool. Returns False if already pending."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pending_nominations (game_id, nominated_by) VALUES (?, ?)",
            (game_id, nominated_by),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_pending_nominations() -> list[sqlite3.Row]:
    """Get all pending nominations with game names."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT pn.*, g.name AS game_name FROM pending_nominations pn "
        "JOIN games g ON g.id = pn.game_id ORDER BY pn.nominated_at",
    ).fetchall()
    conn.close()
    return rows


def get_pending_nomination_count_for_user(user_id: int) -> int:
    """Count how many pending nominations a user has."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM pending_nominations WHERE nominated_by = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["cnt"]


def get_pending_nomination_count() -> int:
    """Total number of pending nominations."""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) AS cnt FROM pending_nominations").fetchone()
    conn.close()
    return row["cnt"]


def absorb_pending_nominations(cycle_id: int, max_slots: int) -> int:
    """Move pending nominations into a cycle (up to max_slots). Returns count added."""
    conn = get_connection()
    pending = conn.execute(
        "SELECT * FROM pending_nominations ORDER BY nominated_at"
    ).fetchall()

    added = 0
    for row in pending:
        if added >= max_slots:
            break
        try:
            conn.execute(
                "INSERT INTO cycle_games (cycle_id, game_id, is_carry_over, nominated_by) "
                "VALUES (?, ?, 0, ?)",
                (cycle_id, row["game_id"], row["nominated_by"]),
            )
            added += 1
        except sqlite3.IntegrityError:
            # Game already on cycle (e.g. was a carry-over)
            pass

    # Clear all absorbed nominations
    conn.execute("DELETE FROM pending_nominations")
    conn.commit()
    conn.close()
    return added


# ---------------------------------------------------------------------------
# Carry-over helpers
# ---------------------------------------------------------------------------


def get_top_games_from_cycle(cycle_id: int, count: int) -> list[dict]:
    """Get top N games by average rank from a completed cycle (attending voters only)."""
    results = calculate_results(cycle_id)
    return results[:count]
