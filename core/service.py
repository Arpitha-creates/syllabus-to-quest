"""Service layer - the app's backend API.

Both the Streamlit UI and any future HTTP wrapper (FastAPI, Flask, a CLI)
talk to this module instead of poking at the database directly. Each public
function is one user-level action and returns plain dicts, so it is trivial
to expose over JSON later.

    forge()          upload -> topics -> missions -> questions -> revisions
    answer()         grade a single question, award XP
    finish()         close a mission, pay its XP
    dashboard()      everything one screen needs, in one query round
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

from . import db, ingest, nlp, quest, revision

MAX_TOPICS = 30


# --------------------------------------------------------------------------- #
# ingestion pipeline
# --------------------------------------------------------------------------- #
def forge_file(conn: sqlite3.Connection, filename: str, data: bytes,
               start: dt.date | None = None) -> dict:
    text, kind = ingest.extract_text(filename, data)
    return forge_text(conn, filename, text, kind=kind, start=start)


def forge_text(conn: sqlite3.Connection, name: str, text: str,
               kind: str = "txt", start: dt.date | None = None) -> dict:
    """Run the full pipeline on raw text. Returns a summary of what was built."""
    text = nlp.clean_text(text)
    if len(text) < 60:
        raise ValueError("That text is too short to build a quest line from.")

    start = start or dt.date.today()
    document_id = db.add_document(conn, name=name, text=text, kind=kind)

    topics = nlp.extract_topics(text)[:MAX_TOPICS]
    for position, topic in enumerate(topics):
        db.add_topic(
            conn,
            document_id=document_id,
            title=topic["title"],
            body=topic["body"],
            summary=topic["summary"],
            weight=topic["weight"],
            position=position,
        )

    built = quest.build_quests(conn, document_id, start=start)
    revisions = revision.schedule_topics(conn, document_id, start=start)
    challenge = quest.ensure_daily_challenge(conn, start.isoformat())

    return {
        "document_id": document_id,
        "name": name,
        "topics": len(topics),
        "missions": built["missions"],
        "questions": built["questions"],
        "xp_available": built["xp_available"],
        "revisions": revisions,
        "daily_questions": len(json.loads(challenge["question_ids"] or "[]")),
        "topic_titles": [t["title"] for t in topics],
    }


# --------------------------------------------------------------------------- #
# play
# --------------------------------------------------------------------------- #
def answer(conn: sqlite3.Connection, question_id: int, chosen: str) -> dict:
    """Grade one answer. The daily bonus is claimed separately, by the button
    in the UI, so XP is never awarded without the player seeing it."""
    return quest.answer_question(conn, question_id, chosen)


def finish(conn: sqlite3.Connection, mission_id: int) -> dict:
    return quest.finish_mission(conn, mission_id)


def complete_revision(conn: sqlite3.Connection, revision_id: int) -> dict:
    return revision.complete_revision(conn, revision_id)


def exam_plan(conn: sqlite3.Connection, document_id: int,
              exam_date: dt.date) -> dict:
    return revision.plan(conn, document_id, exam_date=exam_date)


# --------------------------------------------------------------------------- #
# read models
# --------------------------------------------------------------------------- #
def profile_snapshot(conn: sqlite3.Connection) -> dict:
    xp = db.total_xp(conn)
    progress = quest.level_progress(xp)
    stats = db.attempt_stats(conn)
    profile = db.get_profile(conn)
    return {
        **progress,
        "rank": quest.rank_title(progress["level"]),
        "name": profile["name"],
        "streak": int(profile["streak"]),
        "accuracy": stats["accuracy"],
        "answered": stats["answered"],
        "correct": stats["correct"],
        "badges": quest.badges(conn),
        "missions_done": conn.execute(
            "SELECT COUNT(*) AS n FROM missions WHERE status = 'done'"
        ).fetchone()["n"],
        "missions_total": conn.execute("SELECT COUNT(*) AS n FROM missions").fetchone()["n"],
    }


def mission_board(conn: sqlite3.Connection, document_id: int | None = None) -> list[dict]:
    rows = db.list_missions(conn, document_id)
    board = []
    for row in rows:
        item = dict(row)
        item["progress"] = quest.mission_progress(conn, row["id"])
        board.append(item)
    return board


def daily(conn: sqlite3.Connection) -> dict:
    return quest.daily_status(conn)


def dashboard(conn: sqlite3.Connection, document_id: int | None = None) -> dict:
    documents = [dict(d) for d in db.list_documents(conn)]
    if document_id is None and documents:
        document_id = documents[0]["id"]
    board = mission_board(conn, document_id) if document_id else []
    return {
        "profile": profile_snapshot(conn),
        "documents": documents,
        "document_id": document_id,
        "missions": board,
        "open_missions": sum(1 for m in board if m["status"] == "open"),
        "done_missions": sum(1 for m in board if m["status"] == "done"),
        "revisions": db.revision_counts(conn),
        "due_revisions": revision.due_today(conn),
        "daily": daily(conn),
        "xp_history": [dict(r) for r in db.xp_history(conn, limit=10)],
    }


def topics_with_questions(conn: sqlite3.Connection, document_id: int) -> list[dict]:
    """Topics plus how many quiz items exist for each - used by the plan view."""
    out = []
    for row in db.list_topics(conn, document_id):
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE topic_id = ?", (row["id"],)
        ).fetchone()["n"]
        out.append({**dict(row), "question_count": int(count)})
    return out
