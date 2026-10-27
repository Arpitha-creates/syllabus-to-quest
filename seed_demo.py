"""Seed a demo database from the bundled samples (no browser needed).

    python seed_demo.py            # forge the DSA syllabus into ./data/quest.db
    python seed_demo.py --notes    # use the prose notes instead
    python seed_demo.py --db /tmp/demo.db --answer 8
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path

from core import db, service

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_data"


def seed(db_path: str | None, sample: str, answer: int) -> None:
    conn = db.connect(db_path) if db_path else db.connect()
    path = SAMPLE_DIR / sample
    result = service.forge_file(conn, path.name, path.read_bytes())

    print(f"Forged '{path.name}'")
    print(f"  units      : {result['topics']}")
    print(f"  missions   : {result['missions']}")
    print(f"  questions  : {result['questions']}")
    print(f"  XP on board: {result['xp_available']}")
    print(f"  revisions  : {result['revisions']}")
    print("\nUnits:")
    for topic in service.topics_with_questions(conn, result["document_id"]):
        print(f"  [{topic['weight']}/5] {topic['title']}  ({topic['question_count']} questions)")

    # optionally answer some questions to prove the XP + streak loop works
    if answer:
        rng = random.Random(7)
        ids = [int(r["id"]) for r in db.random_questions(conn, answer)]
        for qid in ids:
            question = db.get_question(conn, qid)
            choice = question["answer"] if rng.random() < 0.7 else rng.choice(
                db.question_options(question)
            )
            service.answer(conn, qid, choice)
        snapshot = service.profile_snapshot(conn)
        print(f"\nAnswered {len(ids)} questions -> level {snapshot['level']}, "
              f"{snapshot['xp']} XP, {snapshot['accuracy']}% accuracy")

    print("\nDaily challenge:")
    for question in service.daily(conn)["questions"]:
        print("  -", question["prompt"][:100])
    print("\nReady. Start the app with:  streamlit run app.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a Syllabus-to-Quest demo database")
    parser.add_argument("--db", default=None, help="database path (default data/quest.db)")
    parser.add_argument("--notes", action="store_true", help="use the prose notes sample")
    parser.add_argument("--answer", type=int, default=6, help="questions to auto-answer")
    args = parser.parse_args()
    seed(args.db, "sample_notes.txt" if args.notes else "sample_syllabus.txt", args.answer)


if __name__ == "__main__":
    main()
