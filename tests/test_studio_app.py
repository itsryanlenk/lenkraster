"""Headless contracts for the optional LenkRaster Studio desktop UI."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _relative_luminance(color: str) -> float:
    """Return WCAG relative luminance for one strict ``#rrggbb`` token."""
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", color), color
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(left: str, right: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(left), _relative_luminance(right)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_importing_studio_package_does_not_import_tkinter():
    """Library users must not load a windowing runtime by importing the package."""
    source = REPO_ROOT / "src"
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "import lenkraster.studio\n"
        "assert 'tkinter' not in sys.modules\n"
        "assert 'tkinter.ttk' not in sys.modules\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_theme_tokens_have_accessible_foreground_background_pairs():
    """The expressive palette still needs readable text at normal sizes."""
    from lenkraster.studio.theme import COLORS

    required = {
        "background",
        "surface",
        "text",
        "accent",
        "on_accent",
        "focus",
        "on_focus",
        "success",
        "on_success",
        "review",
        "on_review",
        "danger",
        "on_danger",
    }
    assert required <= set(COLORS)

    pairs = (
        ("text", "background"),
        ("text", "surface"),
        ("on_accent", "accent"),
        ("on_focus", "focus"),
        ("on_success", "success"),
        ("on_review", "review"),
        ("on_danger", "danger"),
    )
    for foreground, background in pairs:
        ratio = _contrast(COLORS[foreground], COLORS[background])
        assert ratio >= 4.5, f"{foreground}/{background} contrast is only {ratio:.2f}:1"


def test_every_visible_state_has_an_explicit_text_label():
    """Status may never be communicated by neo-brutalist color alone."""
    from lenkraster.studio.theme import STATUS_LABELS

    assert STATUS_LABELS == {
        "idle": "READY",
        "working": "WORKING",
        "pass": "PASS",
        "review": "REVIEW",
        "error": "ERROR",
    }


def test_startup_failure_is_one_fixed_path_free_message(capsys):
    """Raw Tcl/runtime details must not reach the terminal or user dialog."""
    from lenkraster.studio import app

    private_detail = f"couldn't connect to display {Path.home() / 'private' / 'workstation'}"

    def unavailable_root():
        raise RuntimeError(private_detail)

    result = app.main(root_factory=unavailable_root)
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert captured.err == app.STARTUP_ERROR_MESSAGE + "\n"
    assert app.STARTUP_ERROR_MESSAGE == "LenkRaster Studio could not start."
    assert private_detail not in captured.err
    assert "Users" not in captured.err


def test_pyproject_registers_consoleless_studio_entry_point():
    """Installers should create a windowed executable without changing CLI/MCP."""
    project = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[project.gui-scripts]" in project
    assert 'lenkraster-studio = "lenkraster.studio.app:main"' in project
    assert 'lenkraster = "lenkraster.cli:main"' in project
    assert 'lenkraster-mcp = "lenkraster.mcp_main:main"' in project


def test_controller_rejects_a_second_job_while_busy():
    """Rapid UI actions cannot multiply bounded image work."""
    from lenkraster.studio.controller import StudioBusyError, StudioController

    controller = StudioController()
    token = controller.begin_job()

    assert controller.busy is True
    with pytest.raises(StudioBusyError, match=r"\AStudio is busy\Z"):
        controller.begin_job()

    assert controller.finish_job(token, {"score": 1.0}) is True
    assert controller.busy is False
    assert controller.result == {"score": 1.0}


def test_controller_drops_result_from_an_invalidated_generation():
    """A late worker result cannot replace a newly selected document."""
    from lenkraster.studio.controller import StudioController

    controller = StudioController()
    stale_token = controller.begin_job()
    previous_generation = controller.generation

    current_generation = controller.advance_generation()

    assert current_generation == previous_generation + 1
    assert controller.finish_job(
        stale_token,
        {"path": str(Path.home() / "private" / "stale.png")},
    ) is False
    assert controller.busy is False
    assert controller.result is None

    current_token = controller.begin_job()
    assert controller.finish_job(current_token, {"score": 0.75}) is True
    assert controller.result == {"score": 0.75}
