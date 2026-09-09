"""Public-hygiene contracts for the maintainer screenshot helper."""

from pathlib import Path
import subprocess
import sys

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "capture_studio_screenshot.py"
SCREENSHOT = REPO_ROOT / "docs" / "assets" / "lenkraster-studio.png"


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


def test_documented_primary_button_shadow_gaps_match_their_panels():
    """Transparent ttk corners must not leak the blue button face into the gutter."""
    with Image.open(SCREENSHOT) as opened:
        screenshot = opened.convert("RGB")

    expected_regions = (
        ((1145, 54, 1149, 58), (245, 240, 230), "header lower-left"),
        ((1260, 16, 1264, 20), (245, 240, 230), "header upper-right"),
        ((961, 290, 965, 294), (255, 255, 255), "panel lower-left"),
        ((1239, 252, 1243, 256), (255, 255, 255), "panel upper-right"),
    )
    for box, expected, label in expected_regions:
        pixels = set(screenshot.crop(box).get_flattened_data())
        assert pixels == {expected}, f"{label} shadow gap contains {pixels}"

    accent = (95, 193, 255)
    for point, label in (
        ((1150, 25), "header face"),
        ((970, 260), "panel face"),
    ):
        assert screenshot.getpixel(point) == accent, f"{label} lost its blue fill"
