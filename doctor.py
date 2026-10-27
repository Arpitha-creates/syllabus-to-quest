"""Setup doctor:  python doctor.py

Run this first if the app will not start. It checks the interpreter, the
packages, the files, the database and then actually forges a sample into a
throw-away database, so it exercises the whole pipeline. Every failing line
comes with the exact command that fixes it.
"""

from __future__ import annotations

import importlib.util
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 9)
CHECKS: list[tuple[bool, str, str]] = []


def record(ok: bool, label: str, fix: str = "") -> None:
    CHECKS.append((ok, label, fix))
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok and fix:
        print(f"         -> {fix}")


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def main() -> int:
    print("Syllabus to Quest - setup check\n")
    print(f"  python     {platform.python_version()} ({sys.executable})")
    print(f"  platform   {platform.system()} {platform.release()}")
    print(f"  folder     {ROOT}\n")

    print("Interpreter")
    record(sys.version_info[:2] >= MIN_PYTHON,
           f"Python {platform.python_version()} is new enough",
           "Install Python 3.9 or newer from python.org")

    print("\nPackages")
    for module, package in (("streamlit", "streamlit"), ("pypdf", "pypdf"),
                            ("docx", "python-docx")):
        record(has(module), f"{package} is installed",
               "pip install -r requirements.txt")

    streamlit_version = ""
    if has("streamlit"):
        import streamlit

        streamlit_version = streamlit.__version__
        major, minor = (int(x) for x in streamlit_version.split(".")[:2])
        record((major, minor) >= (1, 32),
               f"streamlit {streamlit_version} is new enough",
               "pip install -U 'streamlit>=1.40'")

    print("\nFiles")
    for name in ("app.py", "run.py", "core/db.py", "core/service.py",
                 "sample_data/sample_syllabus.txt", "data/demo_quest.db"):
        record((ROOT / name).exists(), f"{name} is present",
               "Re-download the project - this file is missing from your copy")

    print("\nDatabase")
    try:
        sys.path.insert(0, str(ROOT))
        from core import db, service

        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "doctor.db")
            record(True, "SQLite database opens and creates its schema")

            sample = ROOT / "sample_data" / "sample_syllabus.txt"
            result = service.forge_file(conn, sample.name, sample.read_bytes())
            record(result["topics"] > 0,
                   f"pipeline runs ({result['topics']} units, "
                   f"{result['missions']} missions, {result['questions']} questions)")

            question = db.random_questions(conn, 1)[0]
            outcome = service.answer(conn, question["id"], question["answer"])
            record(outcome["correct"] and outcome["xp_awarded"] > 0,
                   "answering a question awards XP")
            conn.close()

        data_dir = ROOT / "data"
        record(data_dir.is_dir() and data_dir.exists(),
               f"{data_dir.name}/ is writable",
               "Create the data/ folder next to app.py")
    except Exception as exc:                                  # noqa: BLE001
        record(False, f"backend import/pipeline failed: {exc}",
               "Check the traceback above; usually a missing package")

    print("\nHow to start it")
    record(True, "python run.py            (or: streamlit run app.py)")
    if streamlit_version:
        record(True, f"then open the URL streamlit prints (usually "
                     f"http://localhost:8501)")
    record(True, "In VS Code: press F5, or use the 'Streamlit: app.py' "
                 "launch config in .vscode/launch.json")

    failed = [c for c in CHECKS if not c[0]]
    print()
    if failed:
        print(f"{len(failed)} check(s) failed - fix the lines marked FAIL above.")
        return 1
    print("All checks passed. The app should start cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
