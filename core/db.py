"""SQLite persistence layer for Syllabus-to-Quest.

The whole schema is created on first connect, so a fresh clone needs no
migration step. Every function takes an open connection, which keeps the
layer testable against a throw-away database.

Tables
------
documents        one row per uploaded syllabus / notes file
topics           units pulled out of the document by the NLP layer
missions         quests generated from topics (briefing / drill / boss)
questions        quiz items belonging to a mission
attempts         every answer submitted, right or wrong
xp_events        append-only ledger of experience points
daily_challenges one row per calendar day
revisions        spaced-repetition schedule entries
profile          single row: player name, streak, last active day
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "quest.db"
# A pre-played database shipped with the project, so a first run has something
# to show instead of an empty board. Delete it to start with a blank slate.
DEMO_DB_PATH = PROJECT_ROOT / "data" / "demo_quest.db"

# True when this process started from the demo snapshot rather than a fresh DB.
BOOTSTRAPPED = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'txt',
    text        TEXT    NOT NULL,
    char_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    summary     TEXT    NOT NULL DEFAULT '',
    body        TEXT    NOT NULL DEFAULT '',
    weight      INTEGER NOT NULL DEFAULT 3,
    position    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS missions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    topic_id     INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    title        TEXT    NOT NULL,
    brief        TEXT    NOT NULL DEFAULT '',
    kind         TEXT    NOT NULL DEFAULT 'drill',
    difficulty   TEXT    NOT NULL DEFAULT 'medium',
    xp_reward    INTEGER NOT NULL DEFAULT 40,
    status       TEXT    NOT NULL DEFAULT 'open',
    due_date     TEXT,
    created_at   TEXT    NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  INTEGER REFERENCES missions(id) ON DELETE CASCADE,
    topic_id    INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    qtype       TEXT    NOT NULL DEFAULT 'cloze',
    prompt      TEXT    NOT NULL,
    options     TEXT    NOT NULL,
    answer      TEXT    NOT NULL,
    explanation TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    mission_id  INTEGER REFERENCES missions(id) ON DELETE SET NULL,
    chosen      TEXT    NOT NULL,
    is_correct  INTEGER NOT NULL DEFAULT 0,
    answered_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS xp_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    amount     INTEGER NOT NULL,
    reason     TEXT    NOT NULL,
    ref_id     INTEGER,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_challenges (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    day          TEXT    NOT NULL UNIQUE,
    question_ids TEXT    NOT NULL,
    xp_bonus     INTEGER NOT NULL DEFAULT 25,
    bonus_claimed INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS revisions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id  INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    stage     INTEGER NOT NULL DEFAULT 0,
    due_date  TEXT    NOT NULL,
    done      INTEGER NOT NULL DEFAULT 0,
    done_at   TEXT
);

CREATE TABLE IF NOT EXISTS profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    name            TEXT    NOT NULL DEFAULT 'Learner',
    streak          INTEGER NOT NULL DEFAULT 0,
    last_active_day TEXT,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_doc    ON topics(document_id);
CREATE INDEX IF NOT EXISTS idx_missions_doc  ON missions(document_id);
CREATE INDEX IF NOT EXISTS idx_questions_mis ON questions(mission_id);
CREATE INDEX IF NOT EXISTS idx_attempts_q    ON attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_revisions_due ON revisions(due_date, done);
"""


# --------------------------------------------------------------------------- #
# connection helpers
# --------------------------------------------------------------------------- #
def db_path() -> Path:
    """Database location. Override with the S2Q_DB environment variable."""
    override = os.environ.get("S2Q_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _bootstrap_demo(target: Path) -> bool:
    """Copy the shipped demo database into place on a first run."""
    if os.environ.get("S2Q_DB") or target.exists() or not DEMO_DB_PATH.exists():
        return False
    if target.resolve() == DEMO_DB_PATH.resolve():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(DEMO_DB_PATH, target)
    return True


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and initialise) the database. Safe to call on every UI rerun."""
    global BOOTSTRAPPED
    target = Path(path or db_path())
    if path is None and _bootstrap_demo(target):
        BOOTSTRAPPED = True
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO profile (id, name, streak, created_at) VALUES (1, ?, 0, ?)",
        ("Learner", now()),
    )
    conn.commit()


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return dt.date.today().isoformat()


def reset(conn: sqlite3.Connection) -> None:
    """Wipe every table (used by the 'start over' button)."""
    for table in (
        "attempts", "xp_events", "daily_challenges", "revisions",
        "questions", "missions", "topics", "documents", "profile",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    init_db(conn)


# --------------------------------------------------------------------------- #
# documents + topics
# --------------------------------------------------------------------------- #
def add_document(conn: sqlite3.Connection, name: str, text: str, kind: str = "txt") -> int:
    cur = conn.execute(
        "INSERT INTO documents (name, kind, text, char_count, created_at) VALUES (?,?,?,?,?)",
        (name, kind, text, len(text), now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_documents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()


def get_document(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()


def add_topic(
    conn: sqlite3.Connection,
    document_id: int,
    title: str,
    body: str,
    summary: str = "",
    weight: int = 3,
    position: int = 0,
) -> int:
    cur = conn.execute(
        """INSERT INTO topics (document_id, title, summary, body, weight, position)
           VALUES (?,?,?,?,?,?)""",
        (document_id, title, summary, body, weight, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_topics(conn: sqlite3.Connection, document_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM topics WHERE document_id = ? ORDER BY position, id",
        (document_id,),
    ).fetchall()


def all_topics(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM topics ORDER BY document_id, position").fetchall()


def get_topic(conn: sqlite3.Connection, topic_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()


# --------------------------------------------------------------------------- #
# missions + questions
# --------------------------------------------------------------------------- #
def add_mission(
    conn: sqlite3.Connection,
    document_id: int,
    title: str,
    brief: str = "",
    topic_id: int | None = None,
    kind: str = "drill",
    difficulty: str = "medium",
    xp_reward: int = 40,
    due_date: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO missions
           (document_id, topic_id, title, brief, kind, difficulty, xp_reward, status, due_date, created_at)
           VALUES (?,?,?,?,?,?,?,'open',?,?)""",
        (document_id, topic_id, title, brief, kind, difficulty, xp_reward, due_date, now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_missions(conn: sqlite3.Connection, document_id: int | None = None,
                  status: str | None = None) -> list[sqlite3.Row]:
    sql = """SELECT m.*, t.title AS topic_title
             FROM missions m LEFT JOIN topics t ON t.id = m.topic_id"""
    where, args = [], []
    if document_id is not None:
        where.append("m.document_id = ?")
        args.append(document_id)
    if status is not None:
        where.append("m.status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE m.kind WHEN 'briefing' THEN 0 WHEN 'drill' THEN 1 ELSE 2 END, m.id"
    return conn.execute(sql, args).fetchall()


def get_mission(conn: sqlite3.Connection, mission_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()


def complete_mission(conn: sqlite3.Connection, mission_id: int) -> sqlite3.Row | None:
    """Mark a mission done. Returns the updated row, or None if already done."""
    row = get_mission(conn, mission_id)
    if row is None or row["status"] == "done":
        return None
    conn.execute(
        "UPDATE missions SET status = 'done', completed_at = ? WHERE id = ?",
        (now(), mission_id),
    )
    conn.commit()
    return get_mission(conn, mission_id)


def add_question(
    conn: sqlite3.Connection,
    prompt: str,
    options: Iterable[str],
    answer: str,
    mission_id: int | None = None,
    topic_id: int | None = None,
    qtype: str = "cloze",
    explanation: str = "",
) -> int:
    cur = conn.execute(
        """INSERT INTO questions (mission_id, topic_id, qtype, prompt, options, answer, explanation)
           VALUES (?,?,?,?,?,?,?)""",
        (mission_id, topic_id, qtype, prompt, json.dumps(list(options)), answer, explanation),
    )
    conn.commit()
    return int(cur.lastrowid)


def questions_for_mission(conn: sqlite3.Connection, mission_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM questions WHERE mission_id = ? ORDER BY id", (mission_id,)
    ).fetchall()


def get_question(conn: sqlite3.Connection, question_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()


def random_questions(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM questions ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()


def question_options(row: sqlite3.Row) -> list[str]:
    return json.loads(row["options"])


# --------------------------------------------------------------------------- #
# attempts
# --------------------------------------------------------------------------- #
def record_attempt(conn: sqlite3.Connection, question_id: int, chosen: str,
                   is_correct: bool) -> int:
    q = get_question(conn, question_id)
    mission_id = q["mission_id"] if q else None
    cur = conn.execute(
        """INSERT INTO attempts (question_id, mission_id, chosen, is_correct, answered_at)
           VALUES (?,?,?,?,?)""",
        (question_id, mission_id, chosen, int(is_correct), now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def attempt_stats(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """SELECT COUNT(*) AS answered,
                  COALESCE(SUM(is_correct), 0) AS correct
           FROM attempts"""
    ).fetchone()
    answered, correct = int(row["answered"]), int(row["correct"])
    return {
        "answered": answered,
        "correct": correct,
        "accuracy": round(100 * correct / answered) if answered else 0,
    }


def answered_question_ids(conn: sqlite3.Connection, mission_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT DISTINCT question_id FROM attempts WHERE mission_id = ?", (mission_id,)
    ).fetchall()
    return {int(r["question_id"]) for r in rows}


# --------------------------------------------------------------------------- #
# XP + profile
# --------------------------------------------------------------------------- #
def add_xp(conn: sqlite3.Connection, amount: int, reason: str,
           ref_id: int | None = None) -> int:
    conn.execute(
        "INSERT INTO xp_events (amount, reason, ref_id, created_at) VALUES (?,?,?,?)",
        (int(amount), reason, ref_id, now()),
    )
    conn.commit()
    return total_xp(conn)


def total_xp(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS xp FROM xp_events").fetchone()
    return int(row["xp"])


def xp_history(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM xp_events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def get_profile(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    if row is None:
        init_db(conn)
        row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    return row


def set_profile_name(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("UPDATE profile SET name = ? WHERE id = 1", (name.strip() or "Learner",))
    conn.commit()


def touch_streak(conn: sqlite3.Connection, day: str | None = None) -> int:
    """Register activity for `day` and return the current streak."""
    day = day or today()
    profile = get_profile(conn)
    last = profile["last_active_day"]
    streak = int(profile["streak"])
    if last == day:
        return streak
    yesterday = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    streak = streak + 1 if last == yesterday else 1
    conn.execute(
        "UPDATE profile SET streak = ?, last_active_day = ? WHERE id = 1", (streak, day)
    )
    conn.commit()
    return streak


# --------------------------------------------------------------------------- #
# daily challenges
# --------------------------------------------------------------------------- #
def get_daily_challenge(conn: sqlite3.Connection, day: str | None = None) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM daily_challenges WHERE day = ?", (day or today(),)
    ).fetchone()


def save_daily_challenge(conn: sqlite3.Connection, day: str, question_ids: list[int],
                         xp_bonus: int = 25) -> sqlite3.Row:
    conn.execute(
        """INSERT OR IGNORE INTO daily_challenges (day, question_ids, xp_bonus, created_at)
           VALUES (?,?,?,?)""",
        (day, json.dumps(question_ids), xp_bonus, now()),
    )
    conn.commit()
    return get_daily_challenge(conn, day)


def claim_daily_bonus(conn: sqlite3.Connection, day: str | None = None) -> int:
    """Mark the bonus as claimed; returns XP granted (0 if already claimed)."""
    day = day or today()
    row = get_daily_challenge(conn, day)
    if row is None or row["bonus_claimed"]:
        return 0
    conn.execute("UPDATE daily_challenges SET bonus_claimed = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    return int(row["xp_bonus"])


# --------------------------------------------------------------------------- #
# revisions
# --------------------------------------------------------------------------- #
def add_revision(conn: sqlite3.Connection, topic_id: int, due_date: str,
                 stage: int = 0) -> int:
    cur = conn.execute(
        "INSERT INTO revisions (topic_id, stage, due_date) VALUES (?,?,?)",
        (topic_id, stage, due_date),
    )
    conn.commit()
    return int(cur.lastrowid)


def due_revisions(conn: sqlite3.Connection, day: str | None = None) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*, t.title AS topic_title, t.summary, t.document_id
           FROM revisions r JOIN topics t ON t.id = r.topic_id
           WHERE r.done = 0 AND r.due_date <= ?
           ORDER BY r.due_date, r.id""",
        (day or today(),),
    ).fetchall()


def all_revisions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*, t.title AS topic_title
           FROM revisions r JOIN topics t ON t.id = r.topic_id
           ORDER BY r.due_date, r.id"""
    ).fetchall()


def complete_revision(conn: sqlite3.Connection, revision_id: int) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM revisions WHERE id = ?", (revision_id,)).fetchone()
    if row is None or row["done"]:
        return None
    conn.execute(
        "UPDATE revisions SET done = 1, done_at = ? WHERE id = ?", (now(), revision_id)
    )
    conn.commit()
    return row


def revision_counts(conn: sqlite3.Connection) -> dict[str, int]:
    total = conn.execute("SELECT COUNT(*) AS n FROM revisions").fetchone()["n"]
    done = conn.execute("SELECT COUNT(*) AS n FROM revisions WHERE done = 1").fetchone()["n"]
    due = len(due_revisions(conn))
    return {"total": int(total), "done": int(done), "due": due}
