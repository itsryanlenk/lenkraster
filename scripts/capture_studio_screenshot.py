"""Capture a real, populated LenkRaster Studio window for project documentation.

This is a maintainer-only visual QA helper, not a runtime dependency or an image
mock.  It accepts all machine-specific locations as command-line arguments, uses a
workspace-contained repository example, and writes a metadata-free PNG.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

from PIL import Image, ImageGrab


DEFAULT_SOURCE = Path("docs/examples/hammer-cycle-fixed-palette.png")
CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 820


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture the actual LenkRaster Studio Inspect workbench.",
    )
    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help="Existing trusted workspace containing the example PNG.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Destination .png path; its parent directory must already exist.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Workspace-relative PNG to inspect and display.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing capture explicitly.",
    )
    return parser


def _validated_paths(
    workspace_arg: Path,
    source_arg: Path,
    output_arg: Path,
    *,
    force: bool,
) -> tuple[Path, str, Path]:
    try:
        workspace = workspace_arg.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("workspace is unavailable") from None
    if not workspace.is_dir():
        raise ValueError("workspace is unavailable")
    if source_arg.is_absolute() or any(part == ".." for part in source_arg.parts):
        raise ValueError("source must be workspace-relative")
    try:
        source = (workspace / source_arg).resolve(strict=True)
        source.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("source is unavailable") from None
    if not source.is_file() or source.suffix.lower() != ".png":
        raise ValueError("source must be a PNG")

    output = output_arg.resolve(strict=False)
    try:
        parent = output.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("output directory is unavailable") from None
    if not parent.is_dir() or output.suffix.lower() != ".png":
        raise ValueError("output must be a PNG in an existing directory")
    if output == source:
        raise ValueError("output cannot replace the source")
    if output.exists() and not force:
        raise ValueError("output already exists; pass --force to replace it")
    return workspace, os.fspath(source.relative_to(workspace)), output


def _import_checkout() -> tuple[object, object, object]:
    repo_root = Path(__file__).resolve().parents[1]
    source_root = repo_root / "src"
    source_value = os.fspath(source_root)
    if source_value not in sys.path:
        sys.path.insert(0, source_value)
    from lenkraster.studio import inspect_sprite, load_preview
    from lenkraster.studio.view import StudioWindow

    return StudioWindow, inspect_sprite, load_preview


def _populate_real_inspection(window, inspect_sprite, load_preview, workspace: Path, source: str) -> None:
    report = inspect_sprite(source, trusted_root=workspace)
    preview = load_preview(source, trusted_root=workspace)
    window.workspace = workspace
    window.workspace_text.set("TRUSTED WORKSPACE: DEMO SPRITES")
    window._inspect_source = source
    window.inspect_file_text.set(source.replace("\\", "/"))
    window.notebook.select(window.inspect_tab)
    window._inspection_finished(source.replace("\\", "/"), (report, preview))
    window.inspect_results.see("1.0")
    window._sync_controls()


def _capture_window(workspace: Path, source: str) -> Image.Image:
    StudioWindow, inspect_sprite, load_preview = _import_checkout()
    import tkinter as tk

    root = tk.Tk()
    window = None
    try:
        root.withdraw()
        root.tk.call("tk", "scaling", 1.0)
        window = StudioWindow(root)
        root.geometry(f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}+24+24")
        _populate_real_inspection(
            window,
            inspect_sprite,
            load_preview,
            workspace,
            source,
        )
        root.update_idletasks()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        if screen_width < CAPTURE_WIDTH + 48 or screen_height < CAPTURE_HEIGHT + 48:
            raise RuntimeError("screen is too small for the deterministic capture")
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        time.sleep(0.35)
        root.update()
        left = root.winfo_rootx()
        top = root.winfo_rooty()
        width = root.winfo_width()
        height = root.winfo_height()
        if width != CAPTURE_WIDTH or height != CAPTURE_HEIGHT:
            raise RuntimeError("window did not reach the deterministic capture size")
        grabbed = ImageGrab.grab(
            bbox=(left, top, left + width, top + height),
            include_layered_windows=True,
            all_screens=True,
        )
        clean = Image.new("RGB", grabbed.size)
        clean.paste(grabbed.convert("RGB"))
        return clean
    finally:
        if window is not None:
            window.close()
        else:
            try:
                root.destroy()
            except tk.TclError:
                pass


def _publish(image: Image.Image, output: Path, *, force: bool) -> None:
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=".lenkraster-studio-capture-",
            suffix=".png",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(raw_temporary)
        image.save(temporary, format="PNG", optimize=True)
        with Image.open(temporary) as verified:
            verified.verify()
            if verified.info:
                raise RuntimeError("capture unexpectedly contains PNG metadata")
        if force:
            os.replace(temporary, output)
            temporary = None
            return
        try:
            os.link(temporary, output)
        except FileExistsError:
            raise ValueError("output already exists; pass --force to replace it") from None
        temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        workspace, source, output = _validated_paths(
            args.workspace,
            args.source,
            args.output,
            force=args.force,
        )
        screenshot = _capture_window(workspace, source)
        _publish(screenshot, output, force=args.force)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"capture failed: {error}", file=sys.stderr)
        return 2
    print(f"captured {screenshot.width}x{screenshot.height} Studio window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
