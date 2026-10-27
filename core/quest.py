"""Quest generation, XP rules, levelling, missions and daily challenges.

This is the "game designer" of the app: it turns the topics the NLP layer
found into a mission chain, prices every mission in XP, and hands out the
daily challenge.
"""

from __future__ import annotations

import datetime as dt
import random
import sqlite3
from collections import Counter

from . import db, nlp

# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
DIFFICULTY_MULTIPLIER = {"easy": 1.0, "medium": 1.3, "hard": 1.6}
KIND_BASE_XP = {"briefing": 30, "drill": 45, "boss": 110, "daily": 25}
XP_PER_CORRECT = 10          # for answering a single quiz question
STREAK_BONUS_CAP = 25        # extra XP for a long streak, capped
BOSS_EVERY = 3               # a boss raid after every N units


def mission_xp(kind: str, difficulty: str, question_count: int = 1) -> int:
    base = KIND_BASE_XP.get(kind, 40)
    multiplier = DIFFICULTY_MULTIPLIER.get(difficulty, 1.0)
    return int(round(base * multiplier)) + 5 * max(0, question_count - 1)


def xp_for_level(level: int) -> int:
    """Cumulative XP needed to *reach* `level`. 1 -> 0, 2 -> 100, 3 -> 300 ..."""
    level = max(1, int(level))
    return 50 * (level - 1) * level


def level_for_xp(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


def level_progress(xp: int) -> dict:
    level = level_for_xp(xp)
    floor_xp = xp_for_level(level)
    ceiling_xp = xp_for_level(level + 1)
    span = ceiling_xp - floor_xp
    into_level = xp - floor_xp
    return {
        "level": level,
        "xp": xp,
        "level_floor": floor_xp,
        "next_level_at": ceiling_xp,
        "into_level": into_level,
        "needed": ceiling_xp - xp,
        "pct": round(100 * into_level / span) if span else 100,
    }


def rank_title(level: int) -> str:
    titles = [
        (1, "Apprentice"), (3, "Explorer"), (5, "Pathfinder"), (7, "Scholar"),
        (9, "Strategist"), (12, "Master"), (15, "Grandmaster"),
    ]
    current = "Apprentice"
    for threshold, name in titles:
        if level >= threshold:
            current = name
    return current


def streak_bonus(streak: int) -> int:
    return min(STREAK_BONUS_CAP, max(0, streak - 1) * 5)


# --------------------------------------------------------------------------- #
# mission chain generation
# --------------------------------------------------------------------------- #
def build_quests(conn: sqlite3.Connection, document_id: int,
                 start: dt.date | None = None) -> dict:
    """Create the whole mission chain for one document. Idempotent per document
    only in the sense that callers should forge once; calling twice duplicates."""
    start = start or dt.date.today()
    rows = db.list_topics(conn, document_id)
    topics = [dict(r) for r in rows]
    if not topics:
        return {"missions": 0, "questions": 0, "xp_available": 0}

    pool = nlp.document_pool(topics)
    document = db.get_document(conn, document_id)
    document_text = document["text"] if document else ""
    doc_freq = dict(Counter(w.lower() for w in nlp.tokenize(document_text)))
    created_missions = 0
    created_questions = 0
    xp_available = 0

    for index, topic in enumerate(topics):
        others = [t for t in topics if t["id"] != topic["id"]]
        due = (start + dt.timedelta(days=min(index // 2, 6))).isoformat()

        # --- briefing: read the unit, then one true/false check ---------------
        briefing_items = nlp.true_false_questions(topic, others, limit=1)
        if not briefing_items:
            briefing_items = nlp.recall_questions(topic, others, limit=1)
        briefing_xp = mission_xp("briefing", "easy", len(briefing_items))
        briefing_id = db.add_mission(
            conn,
            document_id=document_id,
            title=f"Briefing: {topic['title']}",
            brief=topic["summary"] or topic["body"][:240],
            topic_id=topic["id"],
            kind="briefing",
            difficulty="easy",
            xp_reward=briefing_xp,
            due_date=due,
        )
        created_missions += 1
        xp_available += briefing_xp
        for item in briefing_items:
            db.add_question(
                conn, prompt=item["prompt"], options=item["options"],
                answer=item["answer"], mission_id=briefing_id,
                topic_id=topic["id"], qtype=item["qtype"],
                explanation=item["explanation"],
            )
            created_questions += 1

        # --- drill: the main quiz for this unit ------------------------------
        drill_items = nlp.build_topic_questions(
            topic, others, pool, n_cloze=4, n_recall=2, doc_freq=doc_freq
        )
        difficulty = "hard" if topic["weight"] >= 5 else (
            "medium" if topic["weight"] >= 3 else "easy"
        )
        drill_xp = mission_xp("drill", difficulty, len(drill_items))
        drill_id = db.add_mission(
            conn,
            document_id=document_id,
            title=f"Drill: {topic['title']}",
            brief=f"Clear {len(drill_items)} questions on this unit to bank "
                  f"{drill_xp} XP. Weight {topic['weight']}/5.",
            topic_id=topic["id"],
            kind="drill",
            difficulty=difficulty,
            xp_reward=drill_xp,
            due_date=due,
        )
        created_missions += 1
        xp_available += drill_xp
        for item in drill_items:
            db.add_question(
                conn, prompt=item["prompt"], options=item["options"],
                answer=item["answer"], mission_id=drill_id,
                topic_id=topic["id"], qtype=item["qtype"],
                explanation=item["explanation"],
            )
            created_questions += 1

        # --- boss raid every N units -----------------------------------------
        if (index + 1) % BOSS_EVERY == 0:
            group = topics[max(0, index - BOSS_EVERY + 1): index + 1]
            boss_items: list[dict] = []
            for member in group:
                siblings = [t for t in topics if t["id"] != member["id"]]
                boss_items.extend(
                    nlp.cloze_questions(member["title"], member["body"], pool, limit=2,
                                        doc_freq=doc_freq)
                )
                boss_items.extend(
                    nlp.recall_questions(member, siblings, limit=1)
                )
            if not boss_items:
                continue
            boss_items = boss_items[:6]
            boss_xp = mission_xp("boss", "hard", len(boss_items))
            names = ", ".join(m["title"] for m in group)
            boss_id = db.add_mission(
                conn,
                document_id=document_id,
                title=f"Boss raid: units {index + 2 - len(group)}-{index + 1}",
                brief=f"Mixed-format raid across: {names}. {boss_xp} XP on the line.",
                topic_id=group[-1]["id"],
                kind="boss",
                difficulty="hard",
                xp_reward=boss_xp,
                due_date=(start + dt.timedelta(days=min(index // 2 + 1, 7))).isoformat(),
            )
            created_missions += 1
            xp_available += boss_xp
            for item in boss_items:
                db.add_question(
                    conn, prompt=item["prompt"], options=item["options"],
                    answer=item["answer"], mission_id=boss_id,
                    topic_id=None, qtype=item["qtype"],
                    explanation=item["explanation"],
                )
                created_questions += 1

    return {
        "missions": created_missions,
        "questions": created_questions,
        "xp_available": xp_available,
    }


# --------------------------------------------------------------------------- #
# answering + mission completion
# --------------------------------------------------------------------------- #
def answer_question(conn: sqlite3.Connection, question_id: int, chosen: str) -> dict:
    """Grade one answer, log it, and award XP if correct."""
    question = db.get_question(conn, question_id)
    if question is None:
        raise ValueError(f"No question with id {question_id}")
    correct = chosen.strip().lower() == question["answer"].strip().lower()
    db.record_attempt(conn, question_id, chosen, correct)
    xp_awarded = 0
    if correct:
        xp_awarded = XP_PER_CORRECT
        db.add_xp(conn, xp_awarded, "Correct answer", question_id)
        streak = db.touch_streak(conn)
    else:
        streak = db.get_profile(conn)["streak"]
    total = db.total_xp(conn)
    return {
        "correct": correct,
        "answer": question["answer"],
        "chosen": chosen,
        "explanation": question["explanation"],
        "xp_awarded": xp_awarded,
        "total_xp": total,
        "level": level_for_xp(total),
        "streak": streak,
    }


def mission_progress(conn: sqlite3.Connection, mission_id: int) -> dict:
    questions = db.questions_for_mission(conn, mission_id)
    answered = db.answered_question_ids(conn, mission_id)
    return {
        "total": len(questions),
        "answered": len(answered),
        "remaining": max(0, len(questions) - len(answered)),
        "ready": bool(questions) and len(answered) >= len(questions),
    }


def finish_mission(conn: sqlite3.Connection, mission_id: int) -> dict:
    """Complete a mission and pay its XP. Requires every question answered."""
    mission = db.get_mission(conn, mission_id)
    if mission is None:
        raise ValueError(f"No mission with id {mission_id}")
    progress = mission_progress(conn, mission_id)
    if not progress["ready"]:
        return {
            "ok": False,
            "reason": f"{progress['remaining']} question(s) still unanswered.",
            "xp_awarded": 0,
            "progress": progress,
        }
    updated = db.complete_mission(conn, mission_id)
    if updated is None:
        return {"ok": False, "reason": "Mission was already complete.",
                "xp_awarded": 0, "progress": progress}
    xp_awarded = int(mission["xp_reward"])
    db.add_xp(conn, xp_awarded, f"Mission complete: {mission['title']}", mission_id)
    streak = db.touch_streak(conn)
    total = db.total_xp(conn)
    return {
        "ok": True,
        "reason": "Mission complete",
        "xp_awarded": xp_awarded,
        "total_xp": total,
        "level": level_for_xp(total),
        "streak": streak,
        "progress": progress,
    }


# --------------------------------------------------------------------------- #
# daily challenge
# --------------------------------------------------------------------------- #
def ensure_daily_challenge(conn: sqlite3.Connection, day: str | None = None) -> sqlite3.Row:
    """Three questions for today: due revisions first, then open missions."""
    day = day or db.today()
    existing = db.get_daily_challenge(conn, day)
    if existing:
        return existing

    picked: list[int] = []
    for revision in db.due_revisions(conn, day):
        for question in conn.execute(
            "SELECT id FROM questions WHERE topic_id = ? ORDER BY RANDOM() LIMIT 1",
            (revision["topic_id"],),
        ):
            picked.append(int(question["id"]))
        if len(picked) >= 3:
            break

    if len(picked) < 3:
        rng = random.Random(nlp.stable_seed("daily", day))
        candidates = conn.execute(
            """SELECT q.id FROM questions q
               JOIN missions m ON m.id = q.mission_id
               WHERE m.status = 'open'"""
        ).fetchall()
        ids = [int(r["id"]) for r in candidates]
        rng.shuffle(ids)
        for qid in ids:
            if qid not in picked:
                picked.append(qid)
            if len(picked) >= 3:
                break

    if not picked:
        picked = [int(r["id"]) for r in db.random_questions(conn, 3)]

    return db.save_daily_challenge(conn, day, picked[:3], xp_bonus=25)


def daily_status(conn: sqlite3.Connection, day: str | None = None) -> dict:
    day = day or db.today()
    challenge = ensure_daily_challenge(conn, day)
    import json as _json

    ids = _json.loads(challenge["question_ids"])
    if not ids:
        return {"day": day, "questions": [], "correct": 0, "total": 0,
                "complete": False, "bonus_claimed": bool(challenge["bonus_claimed"])}

    placeholders = ",".join("?" for _ in ids)
    correct_rows = conn.execute(
        f"""SELECT DISTINCT question_id FROM attempts
            WHERE is_correct = 1 AND question_id IN ({placeholders})""",
        ids,
    ).fetchall()
    correct_ids = {int(r["question_id"]) for r in correct_rows}
    questions = [db.get_question(conn, qid) for qid in ids]
    return {
        "day": day,
        "questions": [q for q in questions if q is not None],
        "correct": len(correct_ids & set(ids)),
        "total": len(ids),
        "complete": len(correct_ids & set(ids)) >= len(ids),
        "bonus_claimed": bool(challenge["bonus_claimed"]),
        "bonus_xp": int(challenge["xp_bonus"]),
    }


def claim_daily_bonus(conn: sqlite3.Connection, day: str | None = None) -> int:
    """Pay the daily bonus if the challenge is fully cleared."""
    day = day or db.today()
    status = daily_status(conn, day)
    if not status["complete"] or status["bonus_claimed"]:
        return 0
    amount = db.claim_daily_bonus(conn, day)
    if amount:
        db.add_xp(conn, amount, f"Daily challenge cleared ({day})", None)
        db.touch_streak(conn, day)
    return amount


# --------------------------------------------------------------------------- #
# badges
# --------------------------------------------------------------------------- #
def badges(conn: sqlite3.Connection) -> list[dict]:
    stats = db.attempt_stats(conn)
    profile = db.get_profile(conn)
    missions_done = conn.execute(
        "SELECT COUNT(*) AS n FROM missions WHERE status = 'done'"
    ).fetchone()["n"]
    boss_done = conn.execute(
        "SELECT COUNT(*) AS n FROM missions WHERE status = 'done' AND kind = 'boss'"
    ).fetchone()["n"]
    revisions = db.revision_counts(conn)
    xp = db.total_xp(conn)

    return [
        {"label": "First strike", "earned": stats["answered"] >= 1,
         "hint": "Answer your first question"},
        {"label": "Quest runner", "earned": missions_done >= 3,
         "hint": "Finish 3 missions"},
        {"label": "Boss slayer", "earned": boss_done >= 1,
         "hint": "Clear a boss raid"},
        {"label": "Sharpshooter", "earned": stats["answered"] >= 10 and stats["accuracy"] >= 80,
         "hint": "80% accuracy over 10+ answers"},
        {"label": "On a roll", "earned": int(profile["streak"]) >= 3,
         "hint": "A 3-day streak"},
        {"label": "Scholar", "earned": xp >= 1000,
         "hint": "Bank 1000 XP"},
        {"label": "No unit left behind",
         "earned": revisions["total"] > 0 and revisions["done"] >= revisions["total"],
         "hint": "Finish every revision"},
    ]
