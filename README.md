# Syllabus to Quest

Turn a syllabus or a set of lecture notes into a playable revision game: unit
missions, generated quizzes, XP and levels, a daily challenge, and a spaced
repetition plan that runs to your exam date.

Upload a PDF, Word file or plain text. The app parses the units, writes a
mission for each one, generates multiple-choice questions from the actual
sentences in your material, prices every mission in XP, and books each unit for
revision 1, 3, 7, 14 and 30 days out.

No API key, no model download, no network call at runtime.

```
syllabus / notes  ->  units  ->  missions  ->  questions  ->  XP  ->  revision plan
```

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens on a pre-played demo (two documents, 19 units, 44 missions, a
4-day streak, badges already earned) so you can see the whole loop before you
upload anything. Use **Forge** to add your own syllabus, or **Profile -> Start
over** to begin clean.

Prefer the command line?

```bash
python seed_demo.py              # forge the DSA sample into data/quest.db
python seed_demo.py --notes      # use the prose lecture notes instead
python build_demo.py             # rebuild the shipped demo snapshot
pytest -q                        # 24 backend tests
```

## What it does

**Units from structure.** Headings such as `Unit 3: Linked Lists`, `Module 2`,
`Chapter 5` and plain numbered lists are detected, with their body text
collected underneath. Administrative sections (textbooks, marks, attendance
policy) are dropped. Notes with no headings fall back to paragraph-level units,
so prose lecture notes work too.

**Questions from your own sentences.** Three item types are generated per unit:

| Type | Shape | Built from |
|---|---|---|
| `cloze` | Fill the blank, four options | A distinctive noun phrase blanked out of a real sentence |
| `recall` | "Which statement belongs to this unit?" | One unit's best sentence against three others |
| `mapping` | "Which unit does this come from?" | A sentence against four unit titles |
| `truefalse` | True or false | A real statement, or one transplanted from another unit |

Distractors come from elsewhere in the same document, so the wrong answers are
topically plausible rather than random. Generation is seeded by a hash of the
prompt, which means the same syllabus always produces the same quiz - and the
tests can assert on it.

**Missions and XP.**

| Kind | Difficulty | XP |
|---|---|---|
| Briefing | easy | 30 |
| Drill | easy / medium / hard | 45 base, x1.3 medium, x1.6 hard |
| Boss raid (every 3 units) | hard | 110 base |

Plus 10 XP per correct answer, 15 + 5xstage XP per revision, and a 25 XP daily
bonus. Levelling is quadratic - level $$n$$ starts at $$50n(n-1)$$ XP, so
level 2 is 100 XP, level 3 is 300, level 5 is 1000 - and a mission only pays out
once every question in it has been answered, so XP cannot be farmed by
re-reading the same question.

**Revision plan.** Intervals of 1, 3, 7, 14 and 30 days. Clearing a revision
promotes the unit one rung and books the next date. The exam planner spreads
units across the first 65% of the days you have left (heaviest first), books
every revision the ladder implies, clips anything past the exam date, and
exports the whole calendar as CSV.

**Daily challenge.** Three questions a day, preferring units whose revision is
due, otherwise anything still open. Clear all three for the bonus.

## Project layout

```
syllabus-to-quest/
├── app.py                  Streamlit UI (quest log theme, inline SVG, no front-end deps)
├── seed_demo.py            CLI: forge a sample into a database
├── build_demo.py           CLI: rebuild data/demo_quest.db (the shipped showcase)
├── core/
│   ├── db.py               SQLite schema + all queries
│   ├── ingest.py           PDF / DOCX / TXT / MD -> clean text
│   ├── nlp.py              keyphrases (RAKE), summariser, topic parser, question generator
│   ├── quest.py            missions, XP rules, levels, badges, daily challenge
│   ├── revision.py         spaced repetition + exam planner
│   └── service.py          backend API the UI calls (forge / answer / finish / dashboard)
├── sample_data/
│   ├── sample_syllabus.txt 10-unit Data Structures syllabus
│   └── sample_notes.txt    prose Operating Systems notes (no headings)
├── data/demo_quest.db      pre-played showcase database
└── tests/test_core.py      24 tests over the whole backend
```

`core/service.py` is the seam: the Streamlit app never touches SQL, so the same
backend can be served over HTTP without changing a line of it.

## Database

SQLite, created on first connect - no migration step.

```
documents   uploaded text and metadata
topics      units (title, body, extractive summary, weight 1-5, position)
missions    quests: kind, difficulty, xp_reward, status, due_date
questions   prompt, JSON options, answer, explanation, qtype
attempts    every answer, right or wrong
xp_events   append-only XP ledger (totals are always SUM'd, never cached)
daily_challenges  one row per day, JSON question ids, bonus_claimed flag
revisions   topic_id, stage, due_date, done
profile     name, streak, last_active_day
```

Delete `data/quest.db` to start from scratch; the demo snapshot is copied back
in on the next run.

## The UI

A "quest log" look rather than default Streamlit chrome: deep ink background,
one mint-to-indigo accent, mission cards with a colour stripe per mission kind
and weight pips, an SVG level ring, a seven-day streak strip, and
**tap-to-answer** option cards that grade on click and then show the correct
answer plus the sentence it came from. The quiz arena is deliberately different
- a batch run you submit once and see scored at the end.

Theme lives in `.streamlit/config.toml`; the rest is CSS and inline SVG in
`app.py`, so nothing is fetched at runtime.

## Extending it

- **Better questions** - `core/nlp.py::cloze_questions` is the only place that
  builds them. Swap in spaCy noun-chunk parsing and every mission improves.
- **More item types** - add a function returning `{qtype, prompt, options,
  answer, explanation}` and call it from `build_topic_questions`.
- **Different pacing** - `core/revision.py::INTERVALS` and
  `core/quest.py::KIND_BASE_XP` hold every number that matters.
- **HTTP API** - wrap the functions in `core/service.py` with FastAPI; they take
  a connection and return dicts.
- **New UI** - replace `app.py`; nothing else knows it exists.

## Notes and limits

- Scanned PDFs have no text layer. The uploader says so instead of producing an
  empty quest board; run OCR first.
- Question quality tracks the source. Bullet-fragment syllabi produce
  fragment-level questions; full sentences produce good ones.
- The NLP is heuristic (RAKE scoring, regex structure parsing, no POS tagger),
  which is what keeps it dependency-free and deterministic. It is not a
  substitute for a comprehension model.
- Single user, single local database - it is a study tool, not a multi-tenant
  service.
