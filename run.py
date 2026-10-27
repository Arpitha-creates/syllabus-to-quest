"""One-command launcher:  python run.py

Works on Windows, macOS and Linux, from any directory, with or without a
virtual environment - it checks the interpreter and dependencies first, then
hands over to `streamlit run app.py`.

    python run.py                  # start the app
    python run.py --port 8502      # different port
    python run.py --install        # pip install -r requirements.txt first
    python run.py --check          # verify the setup and exit (no server)
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 9)
REQUIRED = [("streamlit", "streamlit"), ("pypdf", "pypdf"), ("docx", "python-docx")]


def missing_packages() -> list[str]:
    missing = []
    for module, package in REQUIRED:
        if importlib.util.find_spec(module) is None:
            missing.append(package)
    return missing


def check(verbose: bool = True) -> bool:
    ok = True
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info[:2] < MIN_PYTHON:
        print(f"[x] Python {version} is too old - need "
              f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer.")
        ok = False
    elif verbose:
        print(f"[ok] Python {version} ({sys.executable})")

    missing = missing_packages()
    if missing:
        print(f"[x] Missing packages: {', '.join(missing)}")
        print("    Install them with:  pip install -r requirements.txt")
        print(f"    Or just run:        {Path(sys.executable).name} run.py --install")
        ok = False
    elif verbose:
        print("[ok] streamlit, pypdf and python-docx are installed")

    for name in ("app.py", "core/service.py", "sample_data/sample_syllabus.txt"):
        if not (ROOT / name).exists():
            print(f"[x] {name} is missing - run this from inside the project folder.")
            ok = False
    if ok and verbose:
        print("[ok] project files present")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Syllabus to Quest")
    parser.add_argument("--port", type=int, default=None, help="port to serve on")
    parser.add_argument("--install", action="store_true",
                        help="install requirements.txt before starting")
    parser.add_argument("--check", action="store_true",
                        help="check the setup and exit without starting a server")
    parser.add_argument("--headless", action="store_true",
                        help="do not open a browser (useful on a remote box)")
    args = parser.parse_args()

    if args.install:
        print("Installing dependencies...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r",
                               str(ROOT / "requirements.txt")])

    if not check():
        print("\nSetup is incomplete - fix the lines marked [x] above and retry.")
        return 1

    if args.check:
        print("\nEverything checks out. Start the app with:  python run.py")
        return 0

    command = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py")]
    if args.port:
        command += ["--server.port", str(args.port)]
    if args.headless:
        command += ["--server.headless", "true"]

    print("\nStarting Syllabus to Quest - open the URL printed below.\n")
    try:
        return subprocess.call(command, cwd=str(ROOT))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
