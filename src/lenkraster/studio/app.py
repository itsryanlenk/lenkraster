"""Windowed entry point for LenkRaster Studio.

Tk and the concrete view are imported only while launching the application. This keeps
ordinary library, CLI, and MCP imports independent of the desktop runtime.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Sequence

from .. import __version__


STARTUP_ERROR_MESSAGE = "LenkRaster Studio could not start."
WINDOWS_STARTUP_HELP_MESSAGE = (
    "LenkRaster Studio could not start because the Tcl/Tk desktop runtime is "
    "unavailable. Install an official Python build that includes Tcl/Tk, then try "
    "again."
)


def _default_root_factory():
    import tkinter as tk

    return tk.Tk()


def _show_windows_startup_help() -> None:
    """Show fixed startup guidance when a GUI-script process has no console."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None,
            WINDOWS_STARTUP_HELP_MESSAGE,
            "LenkRaster Studio",
            0x00000010,
        )
    except Exception:
        # Failure to present fallback guidance must not expose platform details.
        return


def _startup_failed(*, show_native_help: bool) -> int:
    print(STARTUP_ERROR_MESSAGE, file=sys.stderr)
    if show_native_help:
        _show_windows_startup_help()
    return 2


def _destroy_root(root) -> None:
    try:
        root.destroy()
    except Exception:
        return


def main(
        argv: Sequence[str] | None = None,
        *,
        root_factory: Callable[[], object] | None = None) -> int:
    """Launch Studio, returning a process exit code without exposing Tk failures."""
    supplied_factory = root_factory is not None
    if argv is None:
        arguments = [] if supplied_factory else list(sys.argv[1:])
    else:
        arguments = list(argv)

    if arguments == ["--version"]:
        print(f"LenkRaster Studio {__version__}")
        return 0
    if arguments:
        return _startup_failed(show_native_help=False)

    factory = root_factory or _default_root_factory
    try:
        root = factory()
    except Exception:
        return _startup_failed(show_native_help=not supplied_factory)

    try:
        from .view import StudioWindow

        window = StudioWindow(root)
        root.mainloop()
        # Retain the application object for the full event-loop lifetime.
        _ = window
    except Exception:
        _destroy_root(root)
        return _startup_failed(show_native_help=False)
    return 0


__all__ = [
    "STARTUP_ERROR_MESSAGE",
    "WINDOWS_STARTUP_HELP_MESSAGE",
    "main",
]
