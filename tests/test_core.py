"""Tests for the Syllabus-to-Quest backend.

    pytest -q

Every test uses a throw-away SQLite file, so the real database is never
touched.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from core import db, ingest, nlp, quest, revision, service

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "sample_syllabus.txt"
NOTES = Path(__file__).resolve().parent.parent / "sample_data" / "sample_notes.txt"


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture()
def forged(conn):
    return service.forge_file(conn, SAMPLE.name, SAMPLE.read_bytes())


# --------------------------------------------------------------------------- #
# NLP
# --------------------------------------------------------------------------- #
def test_extract_topics_finds_structured_units():
    topics = nlp.extract_topics(SAMPLE.read_text())
    assert len(topics) >= 8
    titles = [t["title"] for t in topics]
    assert any("Complexity" in t for t in titles)
    assert any("Graphs" in t for t in titles)
    # course metadata must not become a unit
    assert not any("B.Tech" in t or "Credits" in t for t in titles)
    for topic in topics:
        assert topic["body"].strip()
        assert 1 <= topic["weight"] <= 5


def test_extract_topics_falls_back_to_prose():
    topics = nlp.extract_topics(NOTES.read_text())
    assert len(topics) >= 4
    for topic in topics:
        assert len(topic["body"].split()) >= 10


def test_keyphrases_drop_stopwords():
    phrases = nlp.keyphrases(
        "A binary heap is a complete binary tree stored in an array where every "
        "parent dominates its children."
    )
    assert phrases
    joined = " ".join(phrases).lower()
    assert "the" not in joined.split()
    assert any("binary" in p.lower() or "heap" in p.lower() for p in phrases)


def test_summarize_returns_real_sentences():
    body = ("Paging divides memory into fixed size frames. "
            "A page table maps virtual pages to physical frames. "
            "Thrashing means the system pages more than it computes.")
    summary = nlp.summarize(body, max_sentences=2)
    assert summary
    assert summary in body


def test_cloze_questions_are_answerable():
    body = ("A stack follows last-in-first-out order. "
            "A queue follows first-in-first-out order. "
            "A circular queue wraps the rear index modulo the capacity.")
    pool = nlp.document_pool([{"title": "Stacks", "body": body},
                              {"title": "Queues", "body": body}])
    items = nlp.cloze_questions("Stacks and Queues", body, pool, limit=3)
    assert items, "expected at least one cloze question"
    for item in items:
        options = item["options"]
        assert item["answer"] in options
        assert len(options) == 4
        assert len({o.lower() for o in options}) == 4
        assert "______" in item["prompt"]
        assert item["answer"].lower() not in item["prompt"].lower()


def test_question_generation_is_deterministic():
    body = "A hash function maps a key to an index. Collisions are resolved by chaining."
    pool = nlp.document_pool([{"title": "Hashing", "body": body}])
    first = nlp.cloze_questions("Hashing", body, pool, limit=2)
    second = nlp.cloze_questions("Hashing", body, pool, limit=2)
    assert first == second


# --------------------------------------------------------------------------- #
# XP and levelling
# --------------------------------------------------------------------------- #
def test_level_curve_is_monotonic():
    assert quest.xp_for_level(1) == 0
    assert quest.xp_for_level(2) == 100
    assert quest.xp_for_level(3) == 300
    assert quest.level_for_xp(0) == 1
    assert quest.level_for_xp(99) == 1
    assert quest.level_for_xp(100) == 2
    assert quest.level_for_xp(299) == 2
    assert quest.level_for_xp(300) == 3


def test_level_progress_reports_next_level():
    progress = quest.level_progress(150)
    assert progress["level"] == 2
    assert progress["needed"] == 150
    assert 0 <= progress["pct"] <= 100


def test_mission_xp_scales_with_difficulty():
    easy = quest.mission_xp("drill", "easy", 1)
    hard = quest.mission_xp("drill", "hard", 1)
    more_questions = quest.mission_xp("drill", "easy", 4)
    assert hard > easy
    assert more_questions > easy


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #
def test_forge_builds_missions_questions_and_revisions(conn, forged):
    assert forged["topics"] >= 8
    assert forged["missions"] >= forged["topics"]
    assert forged["questions"] >= forged["topics"] * 3
    assert forged["revisions"] == forged["topics"]

    topics = service.topics_with_questions(conn, forged["document_id"])
    assert all(t["question_count"] > 0 for t in topics)

    board = service.mission_board(conn, forged["document_id"])
    kinds = {m["kind"] for m in board}
    assert {"briefing", "drill", "boss"} <= kinds
    for mission in board:
        assert mission["xp_reward"] > 0
        assert mission["progress"]["total"] >= 1


def test_ingest_rejects_unsupported_extension():
    with pytest.raises(ValueError):
        ingest.extract_text("notes.xlsx", b"whatever")


def test_ingest_rejects_empty_text():
    with pytest.raises(ValueError):
        ingest.extract_text("empty.txt", b"short")


def test_forge_rejects_too_little_text(conn):
    with pytest.raises(ValueError):
        service.forge_text(conn, "tiny", "Unit 1: too small")


# --------------------------------------------------------------------------- #
# answering and missions
# --------------------------------------------------------------------------- #
def test_correct_answer_awards_xp(conn, forged):
    question = db.random_questions(conn, 1)[0]
    before = db.total_xp(conn)
    result = service.answer(conn, question["id"], question["answer"])
    assert result["correct"] is True
    assert result["xp_awarded"] == quest.XP_PER_CORRECT
    assert db.total_xp(conn) == before + quest.XP_PER_CORRECT
    assert result["streak"] >= 1


def test_wrong_answer_awards_nothing(conn, forged):
    question = db.random_questions(conn, 1)[0]
    options = db.question_options(question)
    wrong = next(o for o in options if o != question["answer"])
    before = db.total_xp(conn)
    result = service.answer(conn, question["id"], wrong)
    assert result["correct"] is False
    assert result["xp_awarded"] == 0
    assert db.total_xp(conn) == before
    assert result["answer"] == question["answer"]


def test_mission_pays_only_when_every_question_is_answered(conn, forged):
    mission = db.list_missions(conn, forged["document_id"], status="open")[0]
    questions = db.questions_for_mission(conn, mission["id"])

    blocked = service.finish(conn, mission["id"])
    assert blocked["ok"] is False
    assert blocked["xp_awarded"] == 0

    for question in questions:
        service.answer(conn, question["id"], question["answer"])

    outcome = service.finish(conn, mission["id"])
    assert outcome["ok"] is True
    assert outcome["xp_awarded"] == mission["xp_reward"]

    # paying twice must not be possible
    again = service.finish(conn, mission["id"])
    assert again["ok"] is False


def test_attempt_stats_track_accuracy(conn, forged):
    questions = db.random_questions(conn, 4)
    for question in questions[:3]:
        service.answer(conn, question["id"], question["answer"])
    options = db.question_options(questions[3])
    wrong = next(o for o in options if o != questions[3]["answer"])
    service.answer(conn, questions[3]["id"], wrong)

    stats = db.attempt_stats(conn)
    assert stats["answered"] == 4
    assert stats["correct"] == 3
    assert stats["accuracy"] == 75


# --------------------------------------------------------------------------- #
# daily challenge
# --------------------------------------------------------------------------- #
def test_daily_challenge_has_three_questions_and_is_stable(conn, forged):
    first = service.daily(conn)
    second = service.daily(conn)
    assert first["total"] == 3
    assert [q["id"] for q in first["questions"]] == [q["id"] for q in second["questions"]]


def test_daily_bonus_only_after_all_three_are_cleared(conn, forged):
    status = service.daily(conn)
    assert quest.claim_daily_bonus(conn) == 0      # nothing cleared yet

    for question in status["questions"]:
        service.answer(conn, question["id"], question["answer"])

    bonus = quest.claim_daily_bonus(conn)
    assert bonus == status["bonus_xp"]
    assert quest.claim_daily_bonus(conn) == 0      # cannot claim twice


# --------------------------------------------------------------------------- #
# revisions
# --------------------------------------------------------------------------- #
def test_revision_ladder_advances_and_reschedules(conn, forged):
    due = service.dashboard(conn, forged["document_id"])["due_revisions"]
    if not due:                                    # nothing due today yet
        today = dt.date.today()
        topic = db.list_topics(conn, forged["document_id"])[0]
        db.add_revision(conn, topic["id"], (today - dt.timedelta(days=1)).isoformat(), 0)
        due = service.dashboard(conn, forged["document_id"])["due_revisions"]

    assert due
    outcome = service.complete_revision(conn, due[0]["id"])
    assert outcome["ok"] is True
    assert outcome["xp_awarded"] > 0
    assert outcome["next_due"] is not None

    # the same revision cannot be completed twice
    assert service.complete_revision(conn, due[0]["id"])["ok"] is False


def test_plan_covers_every_unit_within_the_window(conn, forged):
    exam = dt.date.today() + dt.timedelta(days=20)
    plan = service.exam_plan(conn, forged["document_id"], exam)
    studied = [item["title"] for day in plan["days"] for item in day["study"]]
    assert len(studied) == plan["summary"]["topics"]
    assert plan["summary"]["revision_slots"] > 0
    for day in plan["days"]:
        assert day["date"] <= exam.isoformat()
        assert day["minutes"] >= 0


def test_revision_intervals_grow():
    start = dt.date(2026, 1, 1)
    dates = revision.revision_ladder(1, start)
    assert dates[0] == "2026-01-01"                # the first due date as given
    assert dates[1] == "2026-01-04"                # +3 days
    gaps = [
        (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
        for a, b in zip(dates, dates[1:])
    ]
    assert gaps == sorted(gaps) and gaps[0] > 0


# --------------------------------------------------------------------------- #
# read models
# --------------------------------------------------------------------------- #
def test_dashboard_shape(conn, forged):
    data = service.dashboard(conn, forged["document_id"])
    assert data["profile"]["level"] >= 1
    assert data["documents"]
    assert data["missions"]
    assert "daily" in data and data["daily"]["total"] == 3
    assert isinstance(data["due_revisions"], list)


def test_badges_start_locked(conn):
    earned = [b["label"] for b in quest.badges(conn) if b["earned"]]
    assert earned == []
