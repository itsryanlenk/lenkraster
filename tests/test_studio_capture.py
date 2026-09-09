"""Public-hygiene contracts for the maintainer screenshot helper."""

from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "capture_studio_screenshot.py"


def test_capture_helper_imports_without_starting_tk():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy, sys; "
                f"runpy.run_path({str(SCRIPT)!r}, run_name='capture_module'); "
                "assert 'tkinter' not in sys.modules"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_capture_helper_contains_no_machine_specific_path():
    source = SCRIPT.read_text(encoding="utf-8").lower()

    assert "c:\\users" not in source
    assert "program files" not in source
    assert "inkscape" not in source
