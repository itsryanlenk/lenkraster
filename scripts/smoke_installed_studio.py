#!/usr/bin/env python3
"""Exercise an installed LenkRaster Studio package through its real Tk view."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import tkinter as tk

from PIL import Image

from lenkraster.studio import load_user_palette
from lenkraster.studio.view import StudioWindow


def _wait_for_job(root: tk.Tk, window: StudioWindow) -> None:
    deadline = time.monotonic() + 15
    while window.controller.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    root.update()
    if window.controller.busy:
        raise RuntimeError("Studio smoke job timed out")
    if "STATUS: ERROR" in window.status_text.get():
        raise RuntimeError("Studio smoke job failed")


def _save_frame(path: Path, color: tuple[int, int, int, int]) -> None:
    Image.new("RGBA", (16, 16), color).save(path, format="PNG")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lenkraster-installed-studio-") as raw_root:
        workspace = Path(raw_root)
        _save_frame(workspace / "frame-0.png", (95, 193, 255, 255))
        _save_frame(workspace / "frame-1.png", (255, 138, 0, 255))
        (workspace / "palette.json").write_text(
            json.dumps({
                "name": "Smoke palette",
                "author": "",
                "colors": ["000000", "5fc1ff", "ff8a00", "ffffff"],
            }),
            encoding="utf-8",
        )

        root = tk.Tk()
        root.withdraw()
        window = StudioWindow(root)
        try:
            window.workspace = workspace
            window._sync_controls()

            window._inspect_source = "frame-0.png"
            window.run_inspection()
            _wait_for_job(root, window)

            palette = load_user_palette("palette.json", trusted_root=workspace)
            window._user_palette_loaded("palette.json", palette)
            window._palette_source = "frame-0.png"
            window.run_palette_preview()
            _wait_for_job(root, window)

            window.run_ramp_preview()
            _wait_for_job(root, window)

            window.run_dither_preview()
            _wait_for_job(root, window)

            window._motion_paths = ["frame-0.png", "frame-1.png"]
            window._motion_images = [
                Image.open(workspace / "frame-0.png").convert("RGBA"),
                Image.open(workspace / "frame-1.png").convert("RGBA"),
            ]
            window.run_motion_qa()
            _wait_for_job(root, window)
        finally:
            window.close()
            try:
                root.update()
            except tk.TclError:
                pass

    print("installed Studio smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
