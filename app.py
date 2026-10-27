"""Syllabus-to-Quest - Streamlit front end.

Run with:  streamlit run app.py

The UI is deliberately thin: every action goes through core.service, which is
the backend API. Swap this file for FastAPI + React and nothing else changes.

Design notes
------------
The look is a "quest log": deep ink background, one accent gradient
(mint -> indigo), numbered chips, a ring for level progress, a seven-day streak
strip, and tap-to-answer option cards instead of a radio + submit pair. All of
it is CSS and inline SVG injected through st.markdown, so there is no extra
front-end dependency and no external font or asset to fetch.
"""

from __future__ import annotations

import datetime as dt
import inspect
import itertools
import math
import sys
from pathlib import Path

# Work no matter which directory the app is started from.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402  (imported after the sys.path fix)

from core import db, quest, service  # noqa: E402


# --------------------------------------------------------------------------- #
# Guard: a Streamlit app must be started with `streamlit run`, not `python`.
# VS Code's Run button does the latter, which serves nothing and only prints
# "missing ScriptRunContext" warnings. Detect that and relaunch properly.
# --------------------------------------------------------------------------- #
def _running_under_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:                      # very old / unexpected streamlit
        return False


def _relaunch_under_streamlit() -> None:
    try:
        from streamlit.web import cli as streamlit_cli
    except ImportError:
        print("\nStreamlit is not installed. From this folder, run:\n"
              "    pip install -r requirements.txt\n"
              "then start the app with:\n"
              "    streamlit run app.py\n")
        raise SystemExit(1)

    print("\n" + "=" * 66)
    print("  Syllabus to Quest is a Streamlit app, so it has to be started")
    print("  with `streamlit run`, not `python app.py`.")
    print("  Starting it properly for you now - your browser will open.")
    print("  (In VS Code: press F5, or run `python run.py`.)")
    print("=" * 66 + "\n")
    sys.argv = ["streamlit", "run", str(Path(__file__).resolve())]
    raise SystemExit(streamlit_cli.main())


if not _running_under_streamlit():
    _relaunch_under_streamlit()


st.set_page_config(
    page_title="Syllabus to Quest",
    layout="wide",
    initial_sidebar_state="expanded",
)

SAMPLE_DIR = PROJECT_ROOT / "sample_data"
_GRADIENT_ID = itertools.count()


def _stretch_kwargs(element) -> dict:
    """Streamlit renamed use_container_width -> width='stretch'; support both."""
    try:
        if "width" in inspect.signature(element).parameters:
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}


STRETCH = _stretch_kwargs(st.button)
DF_STRETCH = _stretch_kwargs(st.dataframe)

KIND_TAG = {"briefing": "Briefing", "drill": "Drill", "boss": "Boss raid"}
KIND_CLASS = {"briefing": "k-briefing", "drill": "k-drill", "boss": "k-boss"}

CSS = """
<style>
:root {
  --ink: #0a0f1e;
  --panel: #111a30;
  --panel-2: #16213c;
  --line: rgba(148, 163, 214, .20);
  --text: #e8eeff;
  --muted: #93a0c4;
  --mint: #6ee7b7;
  --indigo: #818cf8;
  --amber: #fbbf24;
  --rose: #fb7185;
}

.stApp { background: var(--ink); color: var(--text); }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1180px; }

h1, h2, h3, h4 { letter-spacing: -.015em; }
.s2q-kicker { font-size: .70rem; letter-spacing: .22em; text-transform: uppercase;
  color: var(--mint); font-weight: 600; }

/* ---------- hero ---------- */
.s2q-hero { position: relative; overflow: hidden; padding: 1.35rem 1.6rem 1.4rem;
  border-radius: 20px; margin-bottom: 1.25rem; border: 1px solid var(--line);
  background: radial-gradient(120% 160% at 0% 0%, rgba(129,140,248,.20), transparent 55%),
              radial-gradient(120% 160% at 100% 0%, rgba(110,231,183,.16), transparent 55%),
              var(--panel); }
.s2q-hero h1 { margin: .35rem 0 .3rem; font-size: 1.85rem; font-weight: 700; }
.s2q-hero p { margin: 0; color: var(--muted); max-width: 62ch; line-height: 1.5; }

/* ---------- cards ---------- */
.s2q-card { border: 1px solid var(--line); border-radius: 16px; padding: .9rem 1.1rem;
  background: linear-gradient(180deg, var(--panel-2), var(--panel)); }
.s2q-card h4 { margin: 0 0 .35rem; font-size: 1.0rem; font-weight: 650; }
.s2q-muted { color: var(--muted); font-size: .86rem; line-height: 1.45; }

.chip { display: inline-flex; align-items: center; gap: .32rem; font-size: .70rem;
  letter-spacing: .07em; text-transform: uppercase; padding: .18rem .55rem;
  border-radius: 999px; border: 1px solid var(--line); color: var(--muted);
  background: rgba(255,255,255,.03); margin: 0 .3rem .3rem 0; font-weight: 600; }
.chip b { color: var(--text); letter-spacing: 0; text-transform: none; font-size: .78rem; }
.chip.k-briefing { border-color: rgba(129,140,248,.55); color: #b9c1ff; }
.chip.k-drill { border-color: rgba(251,191,36,.5); color: #ffd479; }
.chip.k-boss { border-color: rgba(251,113,133,.55); color: #ff9aab; }
.chip.xp { border-color: rgba(110,231,183,.55); color: var(--mint); }
.chip.done { border-color: rgba(110,231,183,.55); color: var(--mint); }

/* mission card */
.mission { display: grid; grid-template-columns: 6px 1fr; gap: 0 1rem;
  border: 1px solid var(--line); border-radius: 16px; background: var(--panel);
  padding: 0; overflow: hidden; margin-bottom: .1rem; }
.mission .stripe { background: var(--indigo); }
.mission.k-drill .stripe { background: var(--amber); }
.mission.k-boss .stripe { background: var(--rose); }
.mission .body { padding: .85rem 1rem .9rem 0; }
.mission h4 { margin: 0 0 .3rem; font-size: 1rem; }
.pips { letter-spacing: .16em; color: var(--mint); font-size: .78rem; }
.pips .off { color: rgba(148,163,214,.35); }
.bar { height: 6px; border-radius: 99px; background: rgba(148,163,214,.18);
  overflow: hidden; margin-top: .5rem; }
.bar > span { display: block; height: 100%; border-radius: 99px;
  background: linear-gradient(90deg, var(--mint), var(--indigo)); }

/* ---------- level ring ---------- */
.ring { position: relative; display: inline-flex; align-items: center;
  justify-content: center; }
.ring .label { position: absolute; text-align: center; line-height: 1.15; }
.ring .label b { display: block; font-size: 1.32rem; }
.ring .label span { display: block; font-size: .62rem; letter-spacing: .16em;
  text-transform: uppercase; color: var(--muted); }

/* ---------- streak strip ---------- */
.streak { display: flex; gap: .3rem; margin: .55rem 0 .2rem; }
.streak div { flex: 1; text-align: center; font-size: .58rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--muted); }
.streak .cell { height: 26px; border-radius: 7px; border: 1px solid var(--line);
  background: rgba(148,163,214,.10); margin-bottom: .25rem; }
.streak .on { background: linear-gradient(180deg, var(--mint), #34d399);
  border-color: transparent; box-shadow: 0 0 12px rgba(110,231,183,.35); }
.streak .today { outline: 1px solid rgba(129,140,248,.85); outline-offset: 1px; }

/* ---------- question card ---------- */
.qcard { border: 1px solid var(--line); border-radius: 16px; padding: .95rem 1.05rem;
  background: var(--panel); margin-bottom: .5rem; }
.qcard .qhead { display: flex; justify-content: space-between; gap: 1rem;
  align-items: baseline; margin-bottom: .45rem; }
.qcard .qnum { font-size: .68rem; letter-spacing: .16em; text-transform: uppercase;
  color: var(--muted); }
.qcard p { margin: 0; line-height: 1.55; }

.opt { border-radius: 12px; padding: .55rem .8rem; margin: .35rem 0;
  border: 1px solid var(--line); background: rgba(255,255,255,.025); font-size: .92rem; }
.opt.right { border-color: rgba(110,231,183,.7); background: rgba(110,231,183,.12); }
.opt.wrong { border-color: rgba(251,113,133,.7); background: rgba(251,113,133,.12); }
.opt .mark { float: right; font-weight: 700; }
.why { margin-top: .6rem; font-size: .84rem; color: var(--muted);
  border-left: 2px solid var(--line); padding-left: .7rem; }

/* ---------- step chips ---------- */
.steps { display: flex; gap: .5rem; margin: .2rem 0 .8rem; }
.steps div { flex: 1; border: 1px solid var(--line); border-radius: 12px;
  padding: .5rem .7rem; font-size: .78rem; color: var(--muted); background: var(--panel); }
.steps div.on { border-color: rgba(110,231,183,.65); color: var(--mint);
  background: rgba(110,231,183,.10); }

/* ---------- stat tiles ---------- */
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: .7rem; margin: .2rem 0 1rem; }
.tile { border: 1px solid var(--line); border-radius: 14px; padding: .7rem .85rem;
  background: var(--panel); }
.tile span { display: block; font-size: .66rem; letter-spacing: .16em;
  text-transform: uppercase; color: var(--muted); margin-bottom: .25rem; }
.tile b { font-size: 1.32rem; font-variant-numeric: tabular-nums; }
.tile i { font-style: normal; color: var(--muted); font-size: .78rem; }

/* ---------- badges ---------- */
.badge { border: 1px solid var(--line); border-radius: 14px; padding: .7rem .85rem;
  background: var(--panel); height: 100%; }
.badge.earned { border-color: rgba(110,231,183,.55);
  background: linear-gradient(180deg, rgba(110,231,183,.14), var(--panel)); }
.badge b { display: block; font-size: .9rem; margin-bottom: .2rem; }
.badge.locked b { color: var(--muted); }

/* ---------- streamlit widget skin ---------- */
.stButton > button { border-radius: 11px; border: 1px solid var(--line);
  background: var(--panel-2); color: var(--text); font-weight: 550;
  transition: transform .08s ease, border-color .12s ease; }
.stButton > button:hover { border-color: rgba(129,140,248,.75);
  transform: translateY(-1px); color: var(--text); }
.stButton > button[kind="primary"] { background: linear-gradient(90deg, #34d399, #6366f1);
  color: #07101f; border: none; font-weight: 700; }
.stButton > button:disabled { opacity: .45; }
div[data-testid="stSidebarNav"] { display: none; }
section[data-testid="stSidebar"] { background: #0c1327; border-right: 1px solid var(--line); }
section[data-testid="stSidebar"] .block-container { padding-top: 1rem; }
div[data-testid="stExpander"] { border: 1px solid var(--line); border-radius: 14px; }
div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
.stProgress > div > div > div > div { background: linear-gradient(90deg, var(--mint), var(--indigo)); }
hr { border-color: var(--line); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# presentation helpers
# --------------------------------------------------------------------------- #
def hero(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="s2q-hero"><div class="s2q-kicker">{kicker}</div>'
        f"<h1>{title}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def ring(pct: float, big: str, small: str, size: int = 132, stroke: int = 10) -> str:
    """Inline SVG progress ring - no chart library needed."""
    gid = f"g{next(_GRADIENT_ID)}"
    radius = (size - stroke) / 2
    circumference = 2 * math.pi * radius
    offset = circumference * (1 - max(0.0, min(1.0, pct / 100)))
    centre = size / 2
    return (
        f'<div class="ring" style="width:{size}px;height:{size}px">'
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f"<defs><linearGradient id=\"{gid}\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">"
        f'<stop offset="0%" stop-color="#6ee7b7"/><stop offset="100%" stop-color="#818cf8"/>'
        f"</linearGradient></defs>"
        f'<circle cx="{centre}" cy="{centre}" r="{radius}" fill="none" '
        f'stroke="rgba(148,163,214,.18)" stroke-width="{stroke}"/>'
        f'<circle cx="{centre}" cy="{centre}" r="{radius}" fill="none" stroke="url(#{gid})" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-dasharray="{circumference:.1f}" '
        f'stroke-dashoffset="{offset:.1f}" transform="rotate(-90 {centre} {centre})"/>'
        f'</svg><div class="label"><b>{big}</b><span>{small}</span></div></div>'
    )


def tiles(items: list[tuple[str, str, str]]) -> None:
    cells = "".join(
        f'<div class="tile"><span>{label}</span><b>{value}</b> <i>{note}</i></div>'
        for label, value, note in items
    )
    st.markdown(f'<div class="tiles">{cells}</div>', unsafe_allow_html=True)


def chips(items: list[tuple[str, str]]) -> str:
    return "".join(f'<span class="chip {cls}">{text}</span>' for text, cls in items)


def activity_days(conn, span: int = 7) -> list[tuple[dt.date, bool]]:
    rows = conn.execute(
        "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM xp_events"
    ).fetchall()
    active = {row["day"] for row in rows}
    today = dt.date.today()
    return [
        (today - dt.timedelta(days=offset),
         (today - dt.timedelta(days=offset)).isoformat() in active)
        for offset in reversed(range(span))
    ]


def streak_strip(conn) -> str:
    cells = []
    for day, active in activity_days(conn):
        classes = "cell on" if active else "cell"
        if day == dt.date.today():
            classes += " today"
        cells.append(f'<div><div class="{classes}"></div>{day.strftime("%a")[0]}</div>')
    return f'<div class="streak">{"".join(cells)}</div>'


def bar(fraction: float) -> str:
    width = max(0.0, min(1.0, fraction)) * 100
    return f'<div class="bar"><span style="width:{width:.0f}%"></span></div>'


def pips(weight: int) -> str:
    return (f'<span class="pips">{"*" * weight}<span class="off">'
            f'{"*" * (5 - weight)}</span></span>')


# --------------------------------------------------------------------------- #
# state helpers
# --------------------------------------------------------------------------- #
def get_conn():
    if "conn" not in st.session_state:
        st.session_state.conn = db.connect()
    return st.session_state.conn


def latest_attempt(conn, question_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM attempts WHERE question_id = ? ORDER BY id DESC LIMIT 1",
        (question_id,),
    ).fetchone()
    return dict(row) if row else None


def active_document_id(conn) -> int | None:
    documents = [dict(d) for d in db.list_documents(conn)]
    if not documents:
        return None
    if st.session_state.get("doc_id") not in {d["id"] for d in documents}:
        st.session_state.doc_id = documents[0]["id"]
    return st.session_state.doc_id


# --------------------------------------------------------------------------- #
# the question card (tap an option, it grades immediately)
# --------------------------------------------------------------------------- #
def render_question(conn, question, key_prefix: str, index: int | None = None,
                    total: int | None = None) -> None:
    prior = latest_attempt(conn, question["id"])
    options = db.question_options(question)
    counter = f"Question {index} of {total}" if index and total else "Question"

    st.markdown(
        f'<div class="qcard"><div class="qhead">'
        f'<span class="qnum">{counter}</span>'
        f'<span class="qnum">{question["qtype"]}</span></div>'
        f'<p>{question["prompt"]}</p></div>',
        unsafe_allow_html=True,
    )

    if prior:
        chosen = prior["chosen"]
        correct = bool(prior["is_correct"])
        marks = []
        for option in options:
            classes = "opt"
            mark = ""
            if option.strip().lower() == question["answer"].strip().lower():
                classes += " right"
                mark = '<span class="mark">correct</span>'
            elif option.strip().lower() == chosen.strip().lower():
                classes += " wrong"
                mark = '<span class="mark">your pick</span>'
            marks.append(f'<div class="{classes}">{option}{mark}</div>')
        if correct:
            headline = f'<span class="chip xp">+{quest.XP_PER_CORRECT} XP</span>'
        else:
            headline = '<span class="chip">missed this one</span>'
        why = (f'<div class="why">{question["explanation"]}</div>'
               if question["explanation"] else "")
        st.markdown(headline + "".join(marks) + why, unsafe_allow_html=True)
        return

    for position, option in enumerate(options):
        if st.button(option, key=f"{key_prefix}-opt-{question['id']}-{position}",
                     **STRETCH):
            result = service.answer(conn, question["id"], option)
            if result["xp_awarded"]:
                st.toast(f"+{result['xp_awarded']} XP - level {result['level']}")
            st.rerun()


# --------------------------------------------------------------------------- #
# page: forge
# --------------------------------------------------------------------------- #
def page_forge(conn) -> None:
    hero("Step 1 - ingest",
         "Forge a quest line",
         "Drop a syllabus or a set of notes. Every unit becomes a mission, every "
         "mission becomes a quiz, and everything you learn gets a revision date.")

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        uploaded = st.file_uploader(
            "Upload a syllabus or notes", type=["pdf", "docx", "txt", "md"],
            help="PDF with a text layer, Word document, or plain text.",
        )
        pasted = st.text_area("or paste the text", height=150,
                              placeholder="Unit 1: ...\nUnit 2: ...")
        start = st.date_input("Start date", value=dt.date.today())
        if st.button("Forge quests", type="primary",
                     disabled=not (uploaded is not None or pasted.strip())):
            try:
                with st.spinner("Reading units, writing missions and quizzes..."):
                    if uploaded is not None:
                        result = service.forge_file(conn, uploaded.name,
                                                    uploaded.getvalue(), start=start)
                    else:
                        result = service.forge_text(conn, "Pasted notes", pasted,
                                                    start=start)
                st.session_state.doc_id = result["document_id"]
                st.session_state.nav = "Quest board"
                st.session_state.forged = result
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    with right:
        st.markdown('<div class="s2q-card"><h4>No file handy?</h4>'
                    '<div class="s2q-muted">Load one of the bundled samples - a '
                    "structured 10-unit syllabus and a set of prose lecture "
                    "notes, so you can see both parsing paths.</div></div>",
                    unsafe_allow_html=True)
        st.write("")
        samples = {
            "Data Structures syllabus (10 units)": "sample_syllabus.txt",
            "Operating Systems notes (prose)": "sample_notes.txt",
        }
        for label, filename in samples.items():
            if st.button(label, key=f"sample-{filename}", **STRETCH):
                path = SAMPLE_DIR / filename
                result = service.forge_file(conn, filename, path.read_bytes(), start=start)
                st.session_state.doc_id = result["document_id"]
                st.session_state.nav = "Quest board"
                st.session_state.forged = result
                st.rerun()
        st.write("")
        st.markdown(
            '<div class="s2q-muted">Extraction uses pypdf and python-docx. Topic '
            "detection, keyphrase scoring and question generation run locally: "
            "no model download, no API key, no network call.</div>",
            unsafe_allow_html=True,
        )

    forged = st.session_state.get("forged")
    if not forged:
        return

    st.write("")
    st.markdown(f'<div class="s2q-kicker">Built from {forged["name"]}</div>',
                unsafe_allow_html=True)
    tiles([
        ("Units", str(forged["topics"]), "detected"),
        ("Missions", str(forged["missions"]), "queued"),
        ("Questions", str(forged["questions"]), "generated"),
        ("XP on the board", str(forged["xp_available"]), f'{forged["revisions"]} revisions'),
    ])
    rows = service.topics_with_questions(conn, forged["document_id"])
    st.dataframe(
        [{"Unit": r["title"], "Weight": f'{r["weight"]}/5',
          "Questions": r["question_count"], "Extracted summary": r["summary"]}
         for r in rows],
        hide_index=True, **DF_STRETCH,
    )
    if st.button("Open the quest board", type="primary"):
        st.session_state.nav = "Quest board"
        st.rerun()


# --------------------------------------------------------------------------- #
# page: quest board
# --------------------------------------------------------------------------- #
def page_board(conn) -> None:
    doc_id = active_document_id(conn)
    if doc_id is None:
        hero("Step 2 - play", "Quest board",
             "Nothing here yet. Forge a quest line from a syllabus to open the board.")
        return

    active_id = st.session_state.get("active_mission")
    if active_id:
        mission = db.get_mission(conn, active_id)
        if mission is not None:
            render_mission(conn, mission)
            return
        st.session_state.active_mission = None

    board = service.mission_board(conn, doc_id)
    open_missions = [m for m in board if m["status"] == "open"]
    done = [m for m in board if m["status"] == "done"]

    hero("Step 2 - play", "Quest board",
         "Briefings open a unit, drills test it, boss raids mix units together. "
         "A mission pays out once every question in it is answered.")
    tiles([
        ("Open", str(len(open_missions)), "missions"),
        ("Cleared", str(len(done)), "missions"),
        ("XP available", str(sum(m["xp_reward"] for m in open_missions)), "unclaimed"),
        ("Answers logged", str(db.attempt_stats(conn)["answered"]),
         f'{db.attempt_stats(conn)["accuracy"]}% accuracy'),
    ])

    filters = st.columns([2, 1])
    kind_filter = filters[0].multiselect(
        "Show", ["briefing", "drill", "boss"], default=["briefing", "drill", "boss"],
        format_func=lambda k: KIND_TAG[k], label_visibility="collapsed",
    )
    only_open = filters[1].toggle("Only unfinished", value=True)

    shown = [m for m in board
             if m["kind"] in kind_filter and (m["status"] == "open" or not only_open)]
    if not shown:
        st.success("Every mission in this filter is cleared.")
        return

    for mission in shown:
        progress = mission["progress"]
        fraction = progress["answered"] / max(1, progress["total"])
        topic = db.get_topic(conn, mission["topic_id"]) if mission["topic_id"] else None
        weight = int(topic["weight"]) if topic else 3
        left, right = st.columns([4.4, 1])
        with left:
            st.markdown(
                f'<div class="mission {KIND_CLASS.get(mission["kind"], "")}">'
                f'<div class="stripe"></div><div class="body">'
                f'<h4>{mission["title"]}</h4>'
                + chips([
                    (KIND_TAG.get(mission["kind"], mission["kind"]),
                     KIND_CLASS.get(mission["kind"], "")),
                    (mission["difficulty"], ""),
                    (f'{mission["xp_reward"]} XP', "xp"),
                    ("cleared", "done") if mission["status"] == "done" else
                    (mission["due_date"] or "anytime", ""),
                ])
                + f'<div class="s2q-muted">{mission["brief"][:170]}</div>'
                f'<div class="s2q-muted">{progress["answered"]} of '
                f'{progress["total"]} answered &nbsp; {pips(weight)}</div>'
                + bar(fraction) +
                "</div></div>",
                unsafe_allow_html=True,
            )
        label = "Revisit" if mission["status"] == "done" else "Open"
        if right.button(label, key=f"open-{mission['id']}", **STRETCH):
            st.session_state.active_mission = mission["id"]
            st.rerun()


def render_mission(conn, mission) -> None:
    progress = quest.mission_progress(conn, mission["id"])
    if st.button("< Back to the board"):
        st.session_state.active_mission = None
        st.rerun()

    hero(KIND_TAG.get(mission["kind"], mission["kind"]).upper(),
         mission["title"], mission["brief"])
    st.markdown(
        chips([(mission["difficulty"], ""), (f'{mission["xp_reward"]} XP', "xp"),
               (f'{progress["answered"]}/{progress["total"]} answered', ""),
               (mission["due_date"] or "anytime", "")])
        + bar(progress["answered"] / max(1, progress["total"])),
        unsafe_allow_html=True,
    )
    st.write("")

    questions = db.questions_for_mission(conn, mission["id"])
    for index, question in enumerate(questions, start=1):
        render_question(conn, question, key_prefix=f"m{mission['id']}",
                        index=index, total=len(questions))
        st.write("")

    if mission["status"] == "done":
        st.success(f"Mission cleared - {mission['xp_reward']} XP banked.")
    elif progress["ready"]:
        if st.button(f"Claim {mission['xp_reward']} XP", type="primary"):
            outcome = service.finish(conn, mission["id"])
            if outcome["ok"]:
                st.toast(f"Mission cleared: +{outcome['xp_awarded']} XP")
            st.rerun()
    else:
        st.caption(f"{progress['remaining']} question(s) to go before this mission pays out.")


# --------------------------------------------------------------------------- #
# page: daily challenge
# --------------------------------------------------------------------------- #
def page_daily(conn) -> None:
    status = service.daily(conn)
    profile = service.profile_snapshot(conn)

    hero("Today", "Daily challenge",
         "Three questions, drawn fresh each day and always pulled from work you "
         "still have open. Clear all three for a bonus on top of the per-answer XP.")

    steps = []
    for index, question in enumerate(status["questions"], start=1):
        prior = latest_attempt(conn, question["id"])
        state = "on" if prior and prior["is_correct"] else ""
        steps.append(f'<div class="{state}">0{index} - '
                     f'{"cleared" if state else "open"}</div>')
    st.markdown(f'<div class="steps">{"".join(steps)}</div>', unsafe_allow_html=True)

    tiles([
        ("Date", dt.date.today().strftime("%d %b"), "IST"),
        ("Cleared", f'{status["correct"]}/{status["total"]}', "today"),
        ("Streak", str(profile["streak"]), "days"),
        ("Bonus", f'{status["bonus_xp"]} XP',
         "collected" if status["bonus_claimed"] else "locked"),
    ])
    st.markdown(streak_strip(conn), unsafe_allow_html=True)
    st.write("")

    if status["bonus_claimed"]:
        st.success(f"Bonus of {status['bonus_xp']} XP collected today. Come back tomorrow.")
    elif status["complete"]:
        if st.button(f"Collect the +{status['bonus_xp']} XP bonus", type="primary"):
            amount = service.claim_daily_bonus(conn)
            st.toast(f"Daily bonus: +{amount} XP")
            st.rerun()
    else:
        st.caption(f"Clear every question to unlock the +{status['bonus_xp']} XP bonus.")

    for index, question in enumerate(status["questions"], start=1):
        render_question(conn, question, key_prefix="daily",
                        index=index, total=len(status["questions"]))
        st.write("")


# --------------------------------------------------------------------------- #
# page: quiz arena
# --------------------------------------------------------------------------- #
def page_arena(conn) -> None:
    hero("Random run", "Quiz arena",
         "Rapid fire across everything you have uploaded, graded in one go at the "
         f"end. {quest.XP_PER_CORRECT} XP per correct answer.")

    total_questions = conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
    if not total_questions:
        st.info("No questions yet - forge a quest line first.")
        return

    doc_id = active_document_id(conn)
    topics = service.topics_with_questions(conn, doc_id) if doc_id else []
    labels = {t["id"]: t["title"] for t in topics}

    left, right = st.columns([1, 1.4])
    count = left.slider("Questions in this run", 3, min(15, int(total_questions)), 5)
    picked = right.multiselect("Limit to units (optional)", options=list(labels),
                               format_func=lambda i: labels[i])
    if st.button("Start run", type="primary"):
        if picked:
            placeholders = ",".join("?" for _ in picked)
            rows = conn.execute(
                f"SELECT * FROM questions WHERE topic_id IN ({placeholders}) "
                "ORDER BY RANDOM() LIMIT ?", (*picked, count),
            ).fetchall()
        else:
            rows = db.random_questions(conn, count)
        st.session_state.arena = [r["id"] for r in rows]
        st.session_state.arena_result = None

    ids = st.session_state.get("arena")
    if not ids:
        return

    questions = [q for q in (db.get_question(conn, qid) for qid in ids) if q is not None]
    with st.form("arena-form"):
        for index, question in enumerate(questions, start=1):
            st.markdown(
                f'<div class="qcard"><div class="qhead">'
                f'<span class="qnum">Question {index} of {len(questions)}</span>'
                f'<span class="qnum">{question["qtype"]}</span></div>'
                f'<p>{question["prompt"]}</p></div>', unsafe_allow_html=True,
            )
            st.radio("answer", db.question_options(question), index=None,
                     key=f"arena-{question['id']}", label_visibility="collapsed")
        submitted = st.form_submit_button("Submit run", type="primary")

    if submitted:
        score = xp = 0
        detail = []
        for question in questions:
            chosen = st.session_state.get(f"arena-{question['id']}")
            if not chosen:
                detail.append({"Question": question["prompt"][:88],
                               "Result": "skipped", "Answer": question["answer"]})
                continue
            outcome = service.answer(conn, question["id"], chosen)
            score += 1 if outcome["correct"] else 0
            xp += outcome["xp_awarded"]
            detail.append({
                "Question": question["prompt"][:88],
                "Result": "correct" if outcome["correct"] else "wrong",
                "Answer": outcome["answer"],
            })
        st.session_state.arena_result = {
            "score": score, "total": len(questions), "xp": xp, "detail": detail,
        }
        st.rerun()

    result = st.session_state.get("arena_result")
    if result:
        tiles([
            ("Score", f'{result["score"]}/{result["total"]}', "correct"),
            ("XP earned", str(result["xp"]), "this run"),
            ("Accuracy", f'{round(100 * result["score"] / max(1, result["total"]))}%', "run"),
            ("Level", str(quest.level_for_xp(db.total_xp(conn))), "now"),
        ])
        st.dataframe(result["detail"], hide_index=True, **DF_STRETCH)


# --------------------------------------------------------------------------- #
# page: revision plan
# --------------------------------------------------------------------------- #
def page_plan(conn) -> None:
    doc_id = active_document_id(conn)
    hero("Retention", "Revision plan",
         "Every unit comes back after 1, 3, 7, 14 and 30 days. Clear a revision "
         "and it climbs one rung; the plan below lays the whole ladder out up to "
         "your exam date.")

    due = service.dashboard(conn, doc_id)["due_revisions"]
    counts = db.revision_counts(conn)
    tiles([
        ("Due now", str(len(due)), "today"),
        ("Cleared", str(counts["done"]), "revisions"),
        ("Booked", str(counts["total"]), "total"),
        ("Ladder", "1-3-7-14-30", "days"),
    ])

    if not due:
        st.success("Nothing due today. Study ahead on the quest board, or come back tomorrow.")
    for item in due:
        left, right = st.columns([4.2, 1])
        with left:
            st.markdown(
                f'<div class="mission"><div class="stripe"></div><div class="body">'
                f'<h4>{item["topic_title"]}</h4>'
                + chips([(f'stage {item["stage"] + 1} of 5', "k-briefing"),
                         (f'due {item["due_date"]}', "")])
                + f'<div class="s2q-muted">{(item["summary"] or "")[:180]}</div>'
                "</div></div>", unsafe_allow_html=True,
            )
        if right.button("Revised", key=f"rev-{item['id']}", **STRETCH):
            outcome = service.complete_revision(conn, item["id"])
            nxt = outcome["next_due"] or "ladder finished"
            st.toast(f"+{outcome['xp_awarded']} XP - next review {nxt}")
            st.rerun()

    st.write("")
    st.subheader("Plan up to the exam")
    if doc_id is None:
        st.info("Forge a quest line first.")
        return
    left, right = st.columns([1, 2.4])
    exam_date = left.date_input("Exam date",
                                value=dt.date.today() + dt.timedelta(days=21))
    right.caption(
        "Units are spread across the first two thirds of the window, heaviest "
        "first; the last third is revision only. Revisions land 1, 3, 7, 14 and "
        "30 days after the unit is first studied, clipped at the exam date."
    )
    plan = service.exam_plan(conn, doc_id, exam_date)
    summary = plan["summary"]
    tiles([
        ("Units", str(summary["topics"]), "scheduled"),
        ("Study days", str(summary["study_days"]), "of " + str(summary["days"])),
        ("Revision slots", str(summary["revision_slots"]), "booked"),
        ("Minutes/day", str(summary.get("avg_minutes", 0)), "average"),
    ])

    rows = [
        {
            "Day": day["label"],
            "Date": day["date"],
            "Study": "; ".join(f'{s["title"]} ({s["weight"]}/5)' for s in day["study"]) or "-",
            "Revise": "; ".join(f'{r["title"]} [stage {r["stage"]}]' for r in day["revise"]) or "-",
            "Minutes": day["minutes"],
        }
        for day in plan["days"] if not day["rest"]
    ]
    st.dataframe(rows, hide_index=True, **DF_STRETCH, height=400)
    st.download_button("Download plan as CSV", data=_plan_csv(rows),
                       file_name="revision_plan.csv", mime="text/csv")


def _plan_csv(rows: list[dict]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["Day", "Date", "Study", "Revise", "Minutes"])
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# page: profile
# --------------------------------------------------------------------------- #
def page_profile(conn) -> None:
    snapshot = service.profile_snapshot(conn)
    hero("Progress", "Profile",
         "Experience, accuracy, streak and badges. Everything here is derived from "
         "the answer log, so it cannot drift from what you actually did.")

    left, right = st.columns([1, 2.6], gap="large")
    with left:
        st.markdown(ring(snapshot["pct"], str(snapshot["level"]), "level"),
                    unsafe_allow_html=True)
        st.markdown(
            f'<div class="s2q-muted" style="text-align:center">'
            f'{snapshot["rank"]} &middot; {snapshot["into_level"]} / '
            f'{snapshot["needed"] + snapshot["into_level"]} XP into level '
            f'{snapshot["level"]}</div>', unsafe_allow_html=True,
        )
    with right:
        tiles([
            ("Total XP", str(snapshot["xp"]), f'next at {snapshot["next_level_at"]}'),
            ("Streak", str(snapshot["streak"]), "days"),
            ("Accuracy", f'{snapshot["accuracy"]}%', f'{snapshot["answered"]} answers'),
            ("Missions", f'{snapshot["missions_done"]}/{snapshot["missions_total"]}', "cleared"),
        ])
        st.markdown(streak_strip(conn), unsafe_allow_html=True)
        name = st.text_input("Player name", value=snapshot["name"])
        if name != snapshot["name"]:
            db.set_profile_name(conn, name)
            st.rerun()

    st.subheader("Badges")
    badges = snapshot["badges"]
    for start in range(0, len(badges), 4):
        cols = st.columns(4)
        for col, badge in zip(cols, badges[start:start + 4]):
            state = "earned" if badge["earned"] else "locked"
            col.markdown(
                f'<div class="badge {state}"><b>{badge["label"]}</b>'
                f'<div class="s2q-muted">{badge["hint"]}</div></div>',
                unsafe_allow_html=True,
            )

    st.subheader("XP ledger")
    history = db.xp_history(conn, limit=15)
    if history:
        st.dataframe(
            [{"When": row["created_at"].replace("T", " "), "XP": row["amount"],
              "Reason": row["reason"]} for row in history],
            hide_index=True, **DF_STRETCH,
        )
    else:
        st.caption("No XP yet - answer a question on the quest board.")

    with st.expander("Start over"):
        st.caption("Deletes every document, mission, answer and XP event in this database.")
        sure = st.checkbox("I understand this cannot be undone")
        if st.button("Reset everything", disabled=not sure):
            db.reset(conn)
            st.session_state.clear()
            st.rerun()


# --------------------------------------------------------------------------- #
# shell
# --------------------------------------------------------------------------- #
def sidebar(conn) -> str:
    documents = [dict(d) for d in db.list_documents(conn)]
    snapshot = service.profile_snapshot(conn)

    st.sidebar.markdown(
        '<div class="s2q-kicker">Syllabus to Quest</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        f'<div style="display:flex;gap:.9rem;align-items:center;margin:.5rem 0 .2rem">'
        + ring(snapshot["pct"], str(snapshot["level"]), "level", size=74, stroke=7)
        + f'<div><b style="font-size:1rem">{snapshot["rank"]}</b>'
          f'<div class="s2q-muted">{snapshot["xp"]} XP &middot; '
          f'{snapshot["streak"]}d streak<br>{snapshot["answered"]} answers &middot; '
          f'{snapshot["accuracy"]}%</div></div></div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(streak_strip(conn), unsafe_allow_html=True)

    if documents:
        options = {d["id"]: d["name"] for d in documents}
        current = active_document_id(conn)
        keys = list(options)
        st.session_state.doc_id = st.sidebar.selectbox(
            "Active document", keys, index=keys.index(current),
            format_func=lambda k: options[k],
        )

    pages = ["Quest board", "Daily challenge", "Quiz arena", "Revision plan",
             "Profile", "Forge"]
    if st.session_state.get("nav") not in pages:
        st.session_state.nav = "Quest board" if documents else "Forge"
    st.sidebar.radio("Go to", pages, key="nav")
    page = st.session_state.nav
    if page != "Quest board":
        st.session_state.active_mission = None

    if db.BOOTSTRAPPED:
        st.sidebar.markdown(
            '<div class="s2q-muted" style="border:1px dashed rgba(148,163,214,.35);'
            'border-radius:12px;padding:.6rem .7rem;margin-top:.8rem">'
            "You are looking at the bundled demo: two documents, a played "
            "history and a live streak. Use Forge to add your own syllabus, or "
            "Start over in Profile to begin clean.</div>",
            unsafe_allow_html=True,
        )
    return page


def main() -> None:
    conn = get_conn()
    page = sidebar(conn)
    {
        "Quest board": page_board,
        "Daily challenge": page_daily,
        "Quiz arena": page_arena,
        "Revision plan": page_plan,
        "Profile": page_profile,
        "Forge": page_forge,
    }[page](conn)


main()
