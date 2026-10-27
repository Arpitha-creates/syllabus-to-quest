"""Spaced repetition: schedule revisions and build a day-by-day exam plan.

Intervals follow the classic 1 / 3 / 7 / 14 / 30 day ladder. Each completed
revision promotes the topic to the next interval, so a unit you keep getting
right drifts further away instead of piling up.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from . import db

INTERVALS = (1, 3, 7, 14, 30)   # days from the previous touch
STUDY_RATIO = 0.65              # share of days spent learning new units
MINUTES_PER_STUDY = 20
MINUTES_PER_REVISION = 8


def schedule_topics(conn: sqlite3.Connection, document_id: int,
                    start: dt.date | None = None) -> int:
    """Queue the first revision (stage 0) for every unit of a document."""
    start = start or dt.date.today()
    topics = db.list_topics(conn, document_id)
    count = 0
    for index, topic in enumerate(topics):
        # stagger the first pass so day one isn't 12 revisions deep
        offset = INTERVALS[0] + index // 3
        db.add_revision(conn, topic["id"],
                        (start + dt.timedelta(days=offset)).isoformat(), stage=0)
        count += 1
    return count


def complete_revision(conn: sqlite3.Connection, revision_id: int,
                      day: dt.date | None = None) -> dict:
    """Mark a revision done and promote the topic to the next interval."""
    day = day or dt.date.today()
    row = db.complete_revision(conn, revision_id)
    if row is None:
        return {"ok": False, "next_due": None, "stage": None, "xp_awarded": 0}

    stage = int(row["stage"])
    xp_awarded = 15 + 5 * stage
    db.add_xp(conn, xp_awarded, f"Revision done (stage {stage + 1})", row["topic_id"])
    db.touch_streak(conn, day.isoformat())

    next_stage = stage + 1
    if next_stage < len(INTERVALS):
        due = (day + dt.timedelta(days=INTERVALS[next_stage])).isoformat()
        db.add_revision(conn, row["topic_id"], due, stage=next_stage)
        return {"ok": True, "next_due": due, "stage": next_stage + 1,
                "xp_awarded": xp_awarded}

    return {"ok": True, "next_due": None, "stage": next_stage, "xp_awarded": xp_awarded}


def due_today(conn: sqlite3.Connection, day: str | None = None) -> list[dict]:
    return [dict(r) for r in db.due_revisions(conn, day)]


def revision_ladder(topic_id: int, first_due: dt.date) -> list[str]:
    """The full ladder of dates for one topic, starting from its first due date."""
    dates = [first_due.isoformat()]
    cursor = first_due
    for interval in INTERVALS[1:]:
        cursor = cursor + dt.timedelta(days=interval)
        dates.append(cursor.isoformat())
    return dates


def plan(conn: sqlite3.Connection, document_id: int,
         exam_date: dt.date | None = None, days: int | None = None,
         start: dt.date | None = None) -> dict:
    """Build a study + revision calendar that lands on the exam date.

    Units are spread across the first `STUDY_RATIO` of the available days
    (heaviest units earliest), and every studied unit is automatically booked
    for revision 1 / 3 / 7 / 14 / 30 days later, clipped at the exam date.
    """
    start = start or dt.date.today()
    if exam_date is None:
        exam_date = start + dt.timedelta(days=days or 21)
    horizon = max(1, (exam_date - start).days)

    topics = [dict(t) for t in db.list_topics(conn, document_id)]
    if not topics:
        return {"days": [], "summary": {"topics": 0, "days": horizon}}

    # heaviest units first, so the big ones get the earliest slots
    ordered = sorted(topics, key=lambda t: (-t["weight"], t["position"]))
    study_days = max(1, int(round(horizon * STUDY_RATIO)))
    per_day = max(1, -(-len(ordered) // study_days))   # ceil division

    calendar: dict[str, dict] = {}
    for offset in range(horizon + 1):
        day = (start + dt.timedelta(days=offset)).isoformat()
        calendar[day] = {"date": day, "study": [], "revise": [], "minutes": 0}

    for index, topic in enumerate(ordered):
        slot = min(index // per_day, study_days - 1)
        study_day = start + dt.timedelta(days=slot)
        entry = calendar.get(study_day.isoformat())
        if entry is None:
            continue
        entry["study"].append({"title": topic["title"], "weight": topic["weight"],
                               "topic_id": topic["id"]})
        entry["minutes"] += MINUTES_PER_STUDY + 4 * topic["weight"]

        # book the revision ladder for this unit
        cursor = study_day
        for stage, interval in enumerate(INTERVALS):
            cursor = cursor + dt.timedelta(days=interval)
            if cursor > exam_date:
                break
            bucket = calendar.get(cursor.isoformat())
            if bucket is not None:
                bucket["revise"].append({"title": topic["title"], "stage": stage + 1,
                                         "topic_id": topic["id"]})
                bucket["minutes"] += MINUTES_PER_REVISION

    rows = [calendar[(start + dt.timedelta(days=i)).isoformat()] for i in range(horizon + 1)]
    for index, row in enumerate(rows):
        row["day_index"] = index + 1
        row["label"] = f"Day {index + 1}"
        row["is_exam"] = row["date"] == exam_date.isoformat()
        row["rest"] = not row["study"] and not row["revise"]
    total_revisions = sum(len(r["revise"]) for r in rows)
    return {
        "days": rows,
        "summary": {
            "topics": len(topics),
            "days": horizon,
            "study_days": sum(1 for r in rows if r["study"]),
            "revision_slots": total_revisions,
            "exam_date": exam_date.isoformat(),
            "avg_minutes": round(
                sum(r["minutes"] for r in rows) / max(1, len([r for r in rows if not r["rest"]]))
            ),
        },
    }
