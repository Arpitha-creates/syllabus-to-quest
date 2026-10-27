"""Build the bundled showcase database: data/demo_quest.db

    python build_demo.py

The app copies this snapshot into place on first run, so a fresh clone opens
with two documents, a played history, an earned streak and revisions waiting
today - instead of an empty quest board. Re-run this any time to rebuild it.

Everything here goes through the same public API the app uses
(core.service), then backdates the timestamps so the history reads like a
few days of real study.
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

from core import db, revision, service

PROJECT_ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = PROJECT_ROOT / "sample_data"
DEMO_PATH = PROJECT_ROOT / "data" / "demo_quest.db"


def _backdate(conn, table: str, column: str, days: list[int], today: dt.date) -> None:
    """Spread rows of `table` across the last few days, latest rows last."""
    rows = conn.execute(f"SELECT id FROM {table} ORDER BY id").fetchall()
    if not rows:
        return
    for index, row in enumerate(rows):
        slot = min(len(days) - 1, index * len(days) // len(rows))
        day = today + dt.timedelta(days=days[slot])
        stamp = f"{day.isoformat()}T{18 + slot:02d}:{(index * 7) % 60:02d}:00"
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (stamp, row["id"]))
    conn.commit()


def _clear_mission(conn, mission_id: int, correct: bool = True) -> int:
    """Answer every question in a mission, then claim its XP."""
    xp = 0
    for question in db.questions_for_mission(conn, mission_id):
        options = db.question_options(question)
        choice = question["answer"] if correct else options[-1]
        xp += service.answer(conn, question["id"], choice)["xp_awarded"]
    outcome = service.finish(conn, mission_id)
    return xp + int(outcome["xp_awarded"])


def build() -> dict:
    if DEMO_PATH.exists():
        DEMO_PATH.unlink()
    conn = db.connect(DEMO_PATH)
    db.reset(conn)
    today = dt.date.today()

    # ---- two documents, forged a few days apart ---------------------------
    dsa = service.forge_file(conn, "sample_syllabus.txt",
                             (SAMPLE_DIR / "sample_syllabus.txt").read_bytes(),
                             start=today - dt.timedelta(days=3))
    os_notes = service.forge_file(conn, "sample_notes.txt",
                                  (SAMPLE_DIR / "sample_notes.txt").read_bytes(),
                                  start=today - dt.timedelta(days=1))

    # ---- a believable study history on the DSA quest line ----------------
    board = service.mission_board(conn, dsa["document_id"])
    briefings = [m for m in board if m["kind"] == "briefing"]
    drills = [m for m in board if m["kind"] == "drill"]
    bosses = [m for m in board if m["kind"] == "boss"]

    for mission in briefings[:5]:
        _clear_mission(conn, mission["id"])
    for mission in drills[:3]:
        _clear_mission(conn, mission["id"])
    # one drill answered imperfectly, so the accuracy figure is not a flat 100%
    if len(drills) > 3:
        for question in db.questions_for_mission(conn, drills[3]["id"]):
            wrong = next(o for o in db.question_options(question) if o != question["answer"])
            service.answer(conn, question["id"], wrong)
        for question in db.questions_for_mission(conn, drills[3]["id"])[:2]:
            service.answer(conn, question["id"], question["answer"])
        service.finish(conn, drills[3]["id"])

    # ---- the boss raid stays open (something to do) ----------------------
    # and a slice of the OS notes has been worked through
    os_board = service.mission_board(conn, os_notes["document_id"])
    for mission in [m for m in os_board if m["kind"] == "briefing"][:3]:
        _clear_mission(conn, mission["id"])

    # ---- revisions: one cleared, two due today ---------------------------
    dsa_topics = db.list_topics(conn, dsa["document_id"])
    cleared = revision.complete_revision(conn, db.add_revision(
        conn, dsa_topics[0]["id"], (today - dt.timedelta(days=2)).isoformat(), stage=0))
    for topic in dsa_topics[1:3]:
        db.add_revision(conn, topic["id"], today.isoformat(), stage=0)
    for topic in db.list_topics(conn, os_notes["document_id"])[:1]:
        db.add_revision(conn, topic["id"], (today - dt.timedelta(days=1)).isoformat(), stage=0)

    # keep the backlog honest but not overwhelming: clear the oldest few
    overdue = db.due_revisions(conn, today.isoformat())
    for row in overdue[5:]:
        revision.complete_revision(conn, row["id"])

    # ---- today's challenge: fresh, one answer in -------------------------
    conn.execute("DELETE FROM daily_challenges WHERE day = ?", (today.isoformat(),))
    conn.commit()
    answered = {int(r["question_id"]) for r in
                conn.execute("SELECT question_id FROM attempts").fetchall()}
    fresh = [int(r["id"]) for r in
             conn.execute("SELECT id FROM questions ORDER BY RANDOM()").fetchall()
             if int(r["id"]) not in answered][:3]
    if fresh:
        db.save_daily_challenge(conn, today.isoformat(), fresh, xp_bonus=25)
        first = db.get_question(conn, fresh[0])
        service.answer(conn, first["id"], first["answer"])

    # ---- backdate the history so the ledger reads like four days ---------
    _backdate(conn, "attempts", "answered_at", [-3, -2, -1, 0], today)
    _backdate(conn, "xp_events", "created_at", [-3, -2, -1, 0], today)
    conn.execute(
        "UPDATE missions SET completed_at = ? WHERE status = 'done'",
        (f"{(today - dt.timedelta(days=1)).isoformat()}T20:10:00",),
    )
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    conn.execute(
        "UPDATE profile SET name = ?, streak = ?, last_active_day = ? WHERE id = 1",
        ("Demo player", 4, yesterday),
    )
    conn.commit()

    snapshot = service.profile_snapshot(conn)
    stats = db.attempt_stats(conn)
    revisions = db.revision_counts(conn)
    result = {
        "documents": len(db.list_documents(conn)),
        "units": len(db.all_topics(conn)),
        "missions": snapshot["missions_total"],
        "missions_done": snapshot["missions_done"],
        "questions": conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"],
        "answered": stats["answered"],
        "accuracy": stats["accuracy"],
        "xp": snapshot["xp"],
        "level": snapshot["level"],
        "rank": snapshot["rank"],
        "streak": snapshot["streak"],
        "badges": [b["label"] for b in snapshot["badges"] if b["earned"]],
        "revisions": revisions,
        "daily": service.daily(conn),
    }
    conn.close()
    return result


def main() -> None:
    info = build()
    print(f"Wrote {DEMO_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  documents      : {info['documents']}")
    print(f"  units          : {info['units']}")
    print(f"  missions       : {info['missions']} ({info['missions_done']} cleared)")
    print(f"  questions      : {info['questions']} ({info['answered']} answered, "
          f"{info['accuracy']}% accuracy)")
    print(f"  player         : level {info['level']} {info['rank']}, {info['xp']} XP, "
          f"{info['streak']}-day streak")
    print(f"  badges earned  : {', '.join(info['badges']) or 'none'}")
    print(f"  revisions      : {info['revisions']}")
    print(f"  daily challenge: {info['daily']['correct']}/{info['daily']['total']} cleared")
    print("\nRun the app:  streamlit run app.py")


if __name__ == "__main__":
    main()
