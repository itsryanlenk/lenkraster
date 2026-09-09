"""Tk/ttk desktop workbench for LenkRaster Studio.

The view owns presentation and job scheduling only.  All filesystem and image
security decisions remain in :mod:`lenkraster.studio`, and every worker result is
marshalled back through a main-thread poll before Tk or ImageTk is touched.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable

from PIL import Image, ImageTk

from . import (
    MAX_FRAMES,
    MAX_IMAGE_PIXELS,
    MAX_TOTAL_PIXELS,
    StudioError,
    analyze_cycle,
    available_palettes,
    export_aseprite,
    export_dither_png,
    export_quantized_png,
    export_ramp_png,
    inspect_sprite,
    load_palette,
    load_preview,
    load_user_palette,
    preview_dither,
    preview_quantization,
    preview_ramp,
    qa_aseprite,
)
from .controller import JobToken, StudioBusyError, StudioController
from .theme import COLORS, FONTS, METRICS, STATUS_LABELS


_PNG_TYPES = (("PNG images", "*.png"), ("All files", "*.*"))
_PALETTE_TYPES = (("Palette JSON", "*.json"), ("All files", "*.*"))
_ASEPRITE_TYPES = (
    ("Aseprite documents", "*.aseprite *.ase"),
    ("All files", "*.*"),
)
_EXECUTABLE_TYPES = (("Applications", "*.exe"), ("All files", "*.*"))
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_PREVIEW_BOX = (440, 440)
_MOTION_INTERVAL_MS = 180
_POLL_INTERVAL_MS = 35


class StudioWindow:
    """A functional, bounded desktop workbench hosted by one ``tk.Tk`` root."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.controller = StudioController()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="lenkraster-studio",
        )
        self._pending: tuple[
            Future[Any],
            JobToken,
            Callable[[Any], None],
            str,
        ] | None = None
        self._poll_after: str | None = None
        self._play_after: str | None = None
        self._closed = False
        self._close_requested = False
        self._photos: dict[tk.Canvas, ImageTk.PhotoImage] = {}
        self._preview_sources: dict[
            tk.Canvas,
            tuple[Image.Image, tk.StringVar, str],
        ] = {}

        self.workspace: Path | None = None
        self._inspect_source: str | None = None
        self._palette_source: str | None = None
        self._palette_file: str | None = None
        self._motion_paths: list[str] = []
        self._motion_images: list[Image.Image] = []
        self._motion_index = 0
        self._aseprite_document: str | None = None
        self._aseprite_executable: Path | None = None
        self._aseprite_sha256: str | None = None

        self.workspace_text = tk.StringVar(value="NO TRUSTED WORKSPACE SELECTED")
        self.status_text = tk.StringVar(value=f"STATUS: {STATUS_LABELS['idle']} — Choose a workspace.")
        self.inspect_file_text = tk.StringVar(value="No PNG selected")
        self.inspect_preview_text = tk.StringVar(value="Preview: no sprite selected")
        self.palette_file_text = tk.StringVar(value="No PNG selected")
        self.palette_preview_text = tk.StringVar(value="Preview: choose a sprite and palette")
        self.palette_source_text = tk.StringVar(value="Using an original built-in palette")
        self.palette_info_text = tk.StringVar(value="")
        self.ramp_info_text = tk.StringVar(value="Enter a base color to build a material ramp.")
        self.dither_info_text = tk.StringVar(value="Build a bounded ordered-dither test pattern.")
        self.motion_frame_text = tk.StringVar(value="Frame 0 of 0")
        self.motion_preview_text = tk.StringVar(value="Preview: choose two or more ordered PNG frames")
        self.aseprite_document_text = tk.StringVar(value="No Aseprite document selected")
        self.aseprite_executable_text = tk.StringVar(value="No Aseprite executable pinned")

        self._configure_root()
        self._configure_styles()
        self._build_interface()
        self._sync_controls()
        self.root.report_callback_exception = self._report_callback_exception
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<Control-Shift-W>", lambda _event: self.choose_workspace())
        self.root.after_idle(self.workspace_button.focus_set)

    # ------------------------------------------------------------------ shell

    def _configure_root(self) -> None:
        self.root.title("LenkRaster Studio")
        self.root.geometry("1280x820")
        self.root.minsize(960, 640)
        self.root.configure(background=COLORS["background"])

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            ".",
            background=COLORS["background"],
            foreground=COLORS["text"],
            font=FONTS["body"],
        )
        style.configure("Studio.TFrame", background=COLORS["background"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure(
            "Studio.TLabel",
            background=COLORS["background"],
            foreground=COLORS["text"],
        )
        style.configure(
            "Surface.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
        )
        style.configure(
            "Heading.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
        )
        style.configure(
            "Display.TLabel",
            background=COLORS["background"],
            foreground=COLORS["text"],
            font=FONTS["display"],
        )
        style.configure(
            "Advisory.TLabel",
            background=COLORS["text"],
            foreground=COLORS["surface"],
            font=FONTS["body_bold"],
            padding=(METRICS["space_md"], METRICS["space_sm"]),
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["focus"],
            foreground=COLORS["on_focus"],
            font=FONTS["body_bold"],
            padding=(METRICS["space_md"], METRICS["space_sm"]),
        )
        depth = int(METRICS["button_depth"])
        root_images = getattr(self.root, "_lenkraster_button_shadow_images", None)
        if root_images is None:
            image_size = depth * 2 + 1
            normal_shadow = tk.PhotoImage(
                master=self.root,
                width=image_size,
                height=image_size,
            )
            pressed_shadow = tk.PhotoImage(
                master=self.root,
                width=image_size,
                height=image_size,
            )
            disabled_shadow = tk.PhotoImage(
                master=self.root,
                width=image_size,
                height=image_size,
            )
            normal_shadow.put(
                COLORS["shadow"],
                to=(depth + 1, depth, image_size, image_size),
            )
            normal_shadow.put(
                COLORS["shadow"],
                to=(depth, depth + 1, image_size, image_size),
            )
            disabled_shadow.put(
                COLORS["disabled_shadow"],
                to=(depth + 1, depth, image_size, image_size),
            )
            disabled_shadow.put(
                COLORS["disabled_shadow"],
                to=(depth, depth + 1, image_size, image_size),
            )
            root_images = (normal_shadow, pressed_shadow, disabled_shadow)
            self.root._lenkraster_button_shadow_images = root_images
        self._button_shadow_images = root_images
        normal_shadow, pressed_shadow, disabled_shadow = root_images
        base_button_layout = style.layout("TButton")
        shadow_element = "LenkRaster.Button.shadow"
        if shadow_element not in style.element_names():
            style.element_create(
                shadow_element,
                "image",
                normal_shadow,
                ("disabled", disabled_shadow),
                ("pressed", pressed_shadow),
                border=(depth, depth, depth, depth),
                padding=(0, 0, depth, depth),
                sticky="nsew",
            )
        if not base_button_layout or base_button_layout[0][0] != shadow_element:
            style.layout(
                "TButton",
                [
                    (
                        shadow_element,
                        {"sticky": "nsew", "children": base_button_layout},
                    )
                ],
            )
        style.configure(
            "TButton",
            font=FONTS["body_bold"],
            padding=(9, 8),
            borderwidth=METRICS["border"],
            relief="solid",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            focuscolor=COLORS["canvas"],
            focusthickness=2,
        )
        style.map(
            "TButton",
            background=[
                ("disabled", COLORS["disabled_surface"]),
                ("pressed", COLORS["focus"]),
                ("active", COLORS["accent"]),
            ],
            foreground=[("disabled", COLORS["muted_text"])],
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["accent"],
            foreground=COLORS["on_accent"],
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", COLORS["disabled_surface"]),
                ("pressed", COLORS["focus"]),
                ("active", COLORS["focus"]),
            ],
        )
        style.configure(
            "TNotebook",
            background=COLORS["background"],
            borderwidth=0,
            tabmargins=(0, METRICS["space_sm"], 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["body_bold"],
            padding=(18, 12),
            borderwidth=METRICS["border"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["accent"]), ("active", COLORS["focus"])],
        )
        style.configure(
            "TEntry",
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            padding=(8, 9),
            borderwidth=METRICS["border"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            padding=(8, 8),
            borderwidth=METRICS["border"],
        )
        style.configure(
            "TSpinbox",
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            padding=(8, 8),
            borderwidth=METRICS["border"],
        )

    def _build_interface(self) -> None:
        shell = ttk.Frame(self.root, style="Studio.TFrame", padding=METRICS["space_lg"])
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)
        shell.columnconfigure(0, weight=1)

        header = ttk.Frame(shell, style="Studio.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, METRICS["space_sm"]))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="LENKRASTER", style="Display.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, METRICS["space_lg"]),
        )
        ttk.Label(
            header,
            text="PIXEL WORKBENCH / LOCAL + BOUNDED",
            style="Studio.TLabel",
            font=FONTS["technical"],
        ).grid(row=0, column=1, sticky="w")
        self.workspace_button = ttk.Button(
            header,
            text="Choose Workspace",
            command=self.choose_workspace,
            style="Primary.TButton",
            takefocus=True,
        )
        self.workspace_button.grid(row=0, column=2, sticky="e")

        workspace_bar = tk.Frame(
            shell,
            background=COLORS["surface"],
            highlightbackground=COLORS["text"],
            highlightcolor=COLORS["focus"],
            highlightthickness=METRICS["border"],
            padx=METRICS["space_md"],
            pady=METRICS["space_sm"],
        )
        workspace_bar.grid(row=1, column=0, sticky="ew", pady=(0, METRICS["space_sm"]))
        tk.Label(
            workspace_bar,
            textvariable=self.workspace_text,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["technical"],
            anchor="w",
        ).pack(fill="x")

        self.notebook = ttk.Notebook(shell, takefocus=True)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.inspect_tab = ttk.Frame(self.notebook, style="Studio.TFrame")
        self.palette_tab = ttk.Frame(self.notebook, style="Studio.TFrame")
        self.motion_tab = ttk.Frame(self.notebook, style="Studio.TFrame")
        self.aseprite_tab = ttk.Frame(self.notebook, style="Studio.TFrame")
        self.notebook.add(self.inspect_tab, text="1  INSPECT")
        self.notebook.add(self.palette_tab, text="2  PALETTE")
        self.notebook.add(self.motion_tab, text="3  MOTION")
        self.notebook.add(self.aseprite_tab, text="4  ASEPRITE")
        self.notebook.enable_traversal()

        self._build_inspect_tab()
        self._build_palette_tab()
        self._build_motion_tab()
        self._build_aseprite_tab()

        footer = ttk.Frame(shell, style="Studio.TFrame")
        footer.grid(row=3, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, style="Status.TLabel").grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, METRICS["space_sm"]),
        )
        ttk.Label(
            footer,
            text="ADVISORY — Evidence for human review; LenkRaster does not approve artwork.",
            style="Advisory.TLabel",
        ).grid(row=0, column=1, sticky="e")

    def _surface(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent,
            background=COLORS["surface"],
            highlightbackground=COLORS["text"],
            highlightcolor=COLORS["focus"],
            highlightthickness=METRICS["border"],
            padx=METRICS["space_lg"],
            pady=METRICS["space_lg"],
        )

    def _preview_canvas(self, parent: tk.Misc) -> tk.Canvas:
        canvas = tk.Canvas(
            parent,
            width=_PREVIEW_BOX[0],
            height=_PREVIEW_BOX[1],
            background=COLORS["canvas"],
            highlightbackground=COLORS["text"],
            highlightcolor=COLORS["focus"],
            highlightthickness=METRICS["border"],
            takefocus=False,
        )
        canvas.bind("<Configure>", self._canvas_resized, add="+")
        return canvas

    def _results_text(self, parent: tk.Misc, *, height: int = 16) -> tk.Text:
        text = tk.Text(
            parent,
            height=height,
            wrap="word",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent"],
            font=FONTS["technical"],
            highlightbackground=COLORS["text"],
            highlightcolor=COLORS["focus"],
            highlightthickness=METRICS["border"],
            relief="flat",
            padx=METRICS["space_md"],
            pady=METRICS["space_md"],
            takefocus=True,
        )
        text.configure(state="disabled")
        return text

    # --------------------------------------------------------------- tab build

    def _build_inspect_tab(self) -> None:
        tab = self.inspect_tab
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        preview = self._surface(tab)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=6)
        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        tk.Label(
            preview,
            text="SPRITE PREVIEW",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, METRICS["space_md"]))
        self.inspect_canvas = self._preview_canvas(preview)
        self.inspect_canvas.grid(row=1, column=0, sticky="nsew")
        tk.Label(
            preview,
            textvariable=self.inspect_preview_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))

        controls = self._surface(tab)
        controls.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=6)
        controls.columnconfigure(0, weight=1)
        tk.Label(
            controls,
            text="CRAFT + CONTRAST",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            controls,
            textvariable=self.inspect_file_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        buttons = ttk.Frame(controls, style="Surface.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", pady=METRICS["space_md"])
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.inspect_choose_button = ttk.Button(
            buttons,
            text="Choose PNG",
            command=self.choose_inspect_png,
            takefocus=True,
        )
        self.inspect_choose_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.inspect_run_button = ttk.Button(
            buttons,
            text="Inspect Sprite",
            command=self.run_inspection,
            style="Primary.TButton",
            takefocus=True,
        )
        self.inspect_run_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.inspect_results = self._results_text(controls, height=22)
        self.inspect_results.grid(row=3, column=0, sticky="nsew")
        controls.rowconfigure(3, weight=1)

    def _build_palette_tab(self) -> None:
        tab = self.palette_tab
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        preview = self._surface(tab)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=6)
        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        tk.Label(
            preview,
            text="LIVE PIXEL PREVIEW",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, METRICS["space_md"]))
        self.palette_canvas = self._preview_canvas(preview)
        self.palette_canvas.grid(row=1, column=0, sticky="nsew")
        tk.Label(
            preview,
            textvariable=self.palette_preview_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))

        controls = self._surface(tab)
        controls.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=6)
        controls.columnconfigure(0, weight=1)
        controls.rowconfigure(0, weight=1)
        self.palette_modes = ttk.Notebook(controls, takefocus=True)
        self.palette_modes.grid(row=0, column=0, sticky="nsew")
        self.palette_snap_panel = ttk.Frame(
            self.palette_modes,
            style="Surface.TFrame",
            padding=(0, METRICS["space_sm"], 0, 0),
        )
        self.ramp_panel = ttk.Frame(
            self.palette_modes,
            style="Surface.TFrame",
            padding=(0, METRICS["space_sm"], 0, 0),
        )
        self.dither_panel = ttk.Frame(
            self.palette_modes,
            style="Surface.TFrame",
            padding=(0, METRICS["space_sm"], 0, 0),
        )
        self.palette_modes.add(self.palette_snap_panel, text="SNAP")
        self.palette_modes.add(self.ramp_panel, text="RAMP")
        self.palette_modes.add(self.dither_panel, text="DITHER")
        self.palette_modes.enable_traversal()

        self._build_palette_snap_panel()
        self._build_ramp_panel()
        self._build_dither_panel()
        self.palette_mode_actions = {
            "snap": (
                self.palette_snap_panel,
                (self.palette_preview_button, self.palette_export_button),
            ),
            "ramp": (
                self.ramp_panel,
                (self.ramp_preview_button, self.ramp_export_button),
            ),
            "dither": (
                self.dither_panel,
                (self.dither_preview_button, self.dither_export_button),
            ),
        }
        self.root.after_idle(self._draw_palette_swatches)

    def _build_palette_snap_panel(self) -> None:
        panel = self.palette_snap_panel
        panel.columnconfigure(0, weight=1)
        source_row = ttk.Frame(panel, style="Surface.TFrame")
        source_row.grid(row=0, column=0, sticky="ew")
        source_row.columnconfigure(0, weight=1)
        tk.Label(
            source_row,
            textvariable=self.palette_file_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(0, METRICS["space_sm"]))
        self.palette_choose_button = ttk.Button(
            source_row,
            text="Choose PNG",
            command=self.choose_palette_png,
            takefocus=True,
        )
        self.palette_choose_button.grid(row=0, column=1)

        ttk.Label(panel, text="Built-in palette", style="Surface.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(METRICS["space_sm"], 2),
        )
        palette_names = tuple(available_palettes())
        self.palette_name = tk.StringVar(value=palette_names[0] if palette_names else "")
        built_in_row = ttk.Frame(panel, style="Surface.TFrame")
        built_in_row.grid(row=2, column=0, sticky="ew")
        built_in_row.columnconfigure(0, weight=1)
        self.palette_combo = ttk.Combobox(
            built_in_row,
            textvariable=self.palette_name,
            values=palette_names,
            state="readonly",
            takefocus=True,
        )
        self.palette_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.palette_combo.bind("<<ComboboxSelected>>", self._palette_changed)
        self.palette_builtin_button = ttk.Button(
            built_in_row,
            text="Use Built-in",
            command=self._use_builtin_palette,
            takefocus=True,
        )
        self.palette_builtin_button.grid(row=0, column=1, padx=(4, 0))

        user_row = ttk.Frame(panel, style="Surface.TFrame")
        user_row.grid(row=3, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        user_row.columnconfigure(1, weight=1)
        self.palette_json_button = ttk.Button(
            user_row,
            text="Use Palette JSON",
            command=self.choose_palette_json,
            takefocus=True,
        )
        self.palette_json_button.grid(row=0, column=0, padx=(0, METRICS["space_sm"]))
        tk.Label(
            user_row,
            textvariable=self.palette_source_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=0, column=1, sticky="ew")

        self.palette_swatches = tk.Canvas(
            panel,
            height=32,
            background=COLORS["surface"],
            highlightbackground=COLORS["text"],
            highlightthickness=METRICS["border"],
            takefocus=False,
        )
        self.palette_swatches.grid(row=4, column=0, sticky="ew", pady=METRICS["space_sm"])
        tk.Label(
            panel,
            textvariable=self.palette_info_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=5, column=0, sticky="ew")
        palette_buttons = ttk.Frame(panel, style="Surface.TFrame")
        palette_buttons.grid(row=6, column=0, sticky="ew", pady=(METRICS["space_xs"], 0))
        palette_buttons.columnconfigure(0, weight=1)
        palette_buttons.columnconfigure(1, weight=1)
        self.palette_preview_button = ttk.Button(
            palette_buttons,
            text="Preview Palette",
            command=self.run_palette_preview,
            style="Primary.TButton",
            takefocus=True,
        )
        self.palette_preview_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.palette_export_button = ttk.Button(
            palette_buttons,
            text="Export New PNG",
            command=self.export_palette_copy,
            takefocus=True,
        )
        self.palette_export_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _build_ramp_panel(self) -> None:
        panel = self.ramp_panel
        panel.columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text="ORIGINAL MATERIAL RAMP",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        ramp_fields = ttk.Frame(panel, style="Surface.TFrame")
        ramp_fields.grid(row=1, column=0, sticky="ew", pady=METRICS["space_sm"])
        for column in range(3):
            ramp_fields.columnconfigure(column, weight=1)
        ttk.Label(ramp_fields, text="Base hex", style="Surface.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(ramp_fields, text="Stops", style="Surface.TLabel").grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Label(ramp_fields, text="Hue drift", style="Surface.TLabel").grid(
            row=0, column=2, sticky="w", padx=(8, 0)
        )
        self.ramp_base = tk.StringVar(value="#5b6ee1")
        self.ramp_stops = tk.IntVar(value=5)
        self.ramp_drift = tk.DoubleVar(value=-8.0)
        self.ramp_base_entry = ttk.Entry(
            ramp_fields,
            textvariable=self.ramp_base,
            takefocus=True,
        )
        self.ramp_base_entry.grid(row=1, column=0, sticky="ew")
        self.ramp_stops_spinbox = ttk.Spinbox(
            ramp_fields,
            textvariable=self.ramp_stops,
            from_=2,
            to=16,
            increment=1,
            takefocus=True,
        )
        self.ramp_stops_spinbox.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        self.ramp_drift_spinbox = ttk.Spinbox(
            ramp_fields,
            textvariable=self.ramp_drift,
            from_=-45,
            to=45,
            increment=1,
            takefocus=True,
        )
        self.ramp_drift_spinbox.grid(row=1, column=2, sticky="ew", padx=(8, 0))
        self.ramp_swatches = tk.Canvas(
            panel,
            height=54,
            background=COLORS["surface"],
            highlightbackground=COLORS["text"],
            highlightthickness=METRICS["border"],
            takefocus=False,
        )
        self.ramp_swatches.grid(row=2, column=0, sticky="ew")
        tk.Label(
            panel,
            textvariable=self.ramp_info_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(4, 0))
        ramp_buttons = ttk.Frame(panel, style="Surface.TFrame")
        ramp_buttons.grid(row=4, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        ramp_buttons.columnconfigure(0, weight=1)
        ramp_buttons.columnconfigure(1, weight=1)
        self.ramp_preview_button = ttk.Button(
            ramp_buttons,
            text="Preview Ramp",
            command=self.run_ramp_preview,
            style="Primary.TButton",
            takefocus=True,
        )
        self.ramp_preview_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.ramp_export_button = ttk.Button(
            ramp_buttons,
            text="Export Ramp PNG",
            command=self.export_ramp_copy,
            takefocus=True,
        )
        self.ramp_export_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _build_dither_panel(self) -> None:
        panel = self.dither_panel
        panel.columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text="ORDERED BAYER DITHER",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            panel,
            text=(
                "Build a deterministic two-color test pattern. Preview stays in memory; "
                "export creates a new PNG."
            ),
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["body"],
            justify="left",
            anchor="w",
            wraplength=500,
        ).grid(row=1, column=0, sticky="ew", pady=(4, METRICS["space_sm"]))
        fields = ttk.Frame(panel, style="Surface.TFrame")
        fields.grid(row=2, column=0, sticky="ew")
        for column in range(4):
            fields.columnconfigure(column, weight=1)
        for column, label in enumerate(("Color A", "Color B", "Size", "Order")):
            ttk.Label(fields, text=label, style="Surface.TLabel").grid(
                row=0,
                column=column,
                sticky="w",
                padx=(8, 0) if column else 0,
            )
        self.dither_color_a = tk.StringVar(value="#2196f3")
        self.dither_color_b = tk.StringVar(value="#ffeb3b")
        self.dither_size = tk.IntVar(value=128)
        self.dither_order = tk.StringVar(value="4")
        self.dither_color_a_entry = ttk.Entry(
            fields,
            textvariable=self.dither_color_a,
            takefocus=True,
        )
        self.dither_color_a_entry.grid(row=1, column=0, sticky="ew")
        self.dither_color_b_entry = ttk.Entry(
            fields,
            textvariable=self.dither_color_b,
            takefocus=True,
        )
        self.dither_color_b_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        self.dither_size_spinbox = ttk.Spinbox(
            fields,
            textvariable=self.dither_size,
            from_=8,
            to=1024,
            increment=8,
            takefocus=True,
        )
        self.dither_size_spinbox.grid(row=1, column=2, sticky="ew", padx=(8, 0))
        self.dither_order_combo = ttk.Combobox(
            fields,
            textvariable=self.dither_order,
            values=("2", "4"),
            state="readonly",
            takefocus=True,
        )
        self.dither_order_combo.grid(row=1, column=3, sticky="ew", padx=(8, 0))
        tk.Label(
            panel,
            textvariable=self.dither_info_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        buttons = ttk.Frame(panel, style="Surface.TFrame")
        buttons.grid(row=4, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.dither_preview_button = ttk.Button(
            buttons,
            text="Preview Dither",
            command=self.run_dither_preview,
            style="Primary.TButton",
            takefocus=True,
        )
        self.dither_preview_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.dither_export_button = ttk.Button(
            buttons,
            text="Export Dither PNG",
            command=self.export_dither_copy,
            takefocus=True,
        )
        self.dither_export_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _build_motion_tab(self) -> None:
        tab = self.motion_tab
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        preview = self._surface(tab)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=6)
        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        tk.Label(
            preview,
            text="FRAME PLAYER",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, METRICS["space_md"]))
        self.motion_canvas = self._preview_canvas(preview)
        self.motion_canvas.grid(row=1, column=0, sticky="nsew")
        tk.Label(
            preview,
            textvariable=self.motion_preview_text,
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        player = ttk.Frame(preview, style="Surface.TFrame")
        player.grid(row=3, column=0, sticky="ew", pady=(METRICS["space_sm"], 0))
        for column in range(5):
            player.columnconfigure(column, weight=1)
        self.motion_first_button = ttk.Button(
            player, text="First", command=self.motion_first, takefocus=True
        )
        self.motion_prev_button = ttk.Button(
            player, text="Previous", command=self.motion_previous, takefocus=True
        )
        self.motion_play_button = ttk.Button(
            player, text="Play", command=self.toggle_motion_playback, takefocus=True
        )
        self.motion_next_button = ttk.Button(
            player, text="Next", command=self.motion_next, takefocus=True
        )
        self.motion_last_button = ttk.Button(
            player, text="Last", command=self.motion_last, takefocus=True
        )
        for column, button in enumerate(
            (
                self.motion_first_button,
                self.motion_prev_button,
                self.motion_play_button,
                self.motion_next_button,
                self.motion_last_button,
            )
        ):
            button.grid(row=0, column=column, sticky="ew", padx=2)
        ttk.Label(preview, textvariable=self.motion_frame_text, style="Surface.TLabel").grid(
            row=4, column=0, sticky="w", pady=(METRICS["space_sm"], 0)
        )

        controls = self._surface(tab)
        controls.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=6)
        controls.columnconfigure(0, weight=1)
        controls.rowconfigure(2, weight=1)
        tk.Label(
            controls,
            text="ORDERED CYCLE QA",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        self.motion_choose_button = ttk.Button(
            controls,
            text="Choose 2–32 PNG Frames",
            command=self.choose_motion_frames,
            style="Primary.TButton",
            takefocus=True,
        )
        self.motion_choose_button.grid(row=1, column=0, sticky="ew", pady=METRICS["space_sm"])
        list_frame = ttk.Frame(controls, style="Surface.TFrame")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.motion_list = tk.Listbox(
            list_frame,
            exportselection=False,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            selectbackground=COLORS["accent"],
            selectforeground=COLORS["text"],
            font=FONTS["technical"],
            highlightbackground=COLORS["text"],
            highlightcolor=COLORS["focus"],
            highlightthickness=METRICS["border"],
            relief="flat",
            takefocus=True,
        )
        self.motion_list.grid(row=0, column=0, sticky="nsew")
        self.motion_list.bind("<<ListboxSelect>>", self._motion_list_selected)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.motion_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.motion_list.configure(yscrollcommand=scrollbar.set)
        order = ttk.Frame(controls, style="Surface.TFrame")
        order.grid(row=3, column=0, sticky="ew", pady=METRICS["space_sm"])
        for column in range(3):
            order.columnconfigure(column, weight=1)
        self.motion_up_button = ttk.Button(
            order, text="Move Up", command=lambda: self._move_motion_frame(-1), takefocus=True
        )
        self.motion_down_button = ttk.Button(
            order, text="Move Down", command=lambda: self._move_motion_frame(1), takefocus=True
        )
        self.motion_remove_button = ttk.Button(
            order, text="Remove", command=self._remove_motion_frame, takefocus=True
        )
        self.motion_up_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.motion_down_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.motion_remove_button.grid(row=0, column=2, sticky="ew", padx=(3, 0))
        self.motion_qa_button = ttk.Button(
            controls,
            text="Run Bounded Cycle QA",
            command=self.run_motion_qa,
            style="Primary.TButton",
            takefocus=True,
        )
        self.motion_qa_button.grid(row=4, column=0, sticky="ew")
        self.motion_results = self._results_text(controls, height=10)
        self.motion_results.grid(row=5, column=0, sticky="nsew", pady=(METRICS["space_sm"], 0))
        controls.rowconfigure(5, weight=1)

    def _build_aseprite_tab(self) -> None:
        tab = self.aseprite_tab
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)

        panel = self._surface(tab)
        panel.grid(row=0, column=0, sticky="nsew", pady=6)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(10, weight=1)
        tk.Label(
            panel,
            text="ASEPRITE BRIDGE",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["heading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            panel,
            text=(
                "Local batch export only. The chosen executable is SHA-256 pinned for "
                "each run. LenkRaster passes no scripts, uses no shell, and opens no "
                "network listener; the native executable is not sandboxed."
            ),
            background=COLORS["surface"],
            foreground=COLORS["muted_text"],
            font=FONTS["body"],
            justify="left",
            anchor="w",
            wraplength=900,
        ).grid(row=1, column=0, sticky="ew", pady=(4, METRICS["space_md"]))

        selection = ttk.Frame(panel, style="Surface.TFrame")
        selection.grid(row=2, column=0, sticky="ew")
        selection.columnconfigure(0, weight=1)
        tk.Label(
            selection,
            textvariable=self.aseprite_document_text,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(0, METRICS["space_sm"]))
        self.aseprite_document_button = ttk.Button(
            selection,
            text="Choose .aseprite / .ase",
            command=self.choose_aseprite_document,
            takefocus=True,
        )
        self.aseprite_document_button.grid(row=0, column=1)
        tk.Label(
            selection,
            textvariable=self.aseprite_executable_text,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=FONTS["technical"],
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=(0, METRICS["space_sm"]), pady=(8, 0))
        self.aseprite_executable_button = ttk.Button(
            selection,
            text="Choose + Pin Aseprite",
            command=self.choose_aseprite_executable,
            takefocus=True,
        )
        self.aseprite_executable_button.grid(row=1, column=1, pady=(8, 0))

        fields = ttk.Frame(panel, style="Surface.TFrame")
        fields.grid(row=3, column=0, sticky="ew", pady=METRICS["space_md"])
        for column in range(3):
            fields.columnconfigure(column, weight=1)
        ttk.Label(fields, text="Tag (optional)", style="Surface.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(fields, text="Layer (optional)", style="Surface.TLabel").grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Label(fields, text="New relative export directory", style="Surface.TLabel").grid(
            row=0, column=2, sticky="w", padx=(8, 0)
        )
        self.aseprite_tag = tk.StringVar(value="")
        self.aseprite_layer = tk.StringVar(value="")
        self.aseprite_output = tk.StringVar(value="exports/aseprite-export")
        self.aseprite_tag_entry = ttk.Entry(
            fields,
            textvariable=self.aseprite_tag,
            takefocus=True,
        )
        self.aseprite_tag_entry.grid(row=1, column=0, sticky="ew")
        self.aseprite_layer_entry = ttk.Entry(
            fields,
            textvariable=self.aseprite_layer,
            takefocus=True,
        )
        self.aseprite_layer_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        self.aseprite_output_entry = ttk.Entry(
            fields,
            textvariable=self.aseprite_output,
            takefocus=True,
        )
        self.aseprite_output_entry.grid(row=1, column=2, sticky="ew", padx=(8, 0))
        action_row = ttk.Frame(panel, style="Surface.TFrame")
        action_row.grid(row=4, column=0, sticky="ew")
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)
        self.aseprite_qa_button = ttk.Button(
            action_row,
            text="Run Aseprite Cycle QA",
            command=self.run_aseprite_qa,
            style="Primary.TButton",
            takefocus=True,
        )
        self.aseprite_qa_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.aseprite_export_button = ttk.Button(
            action_row,
            text="Export New Sheet + Manifest",
            command=self.run_aseprite_export,
            takefocus=True,
        )
        self.aseprite_export_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(
            panel,
            text=(
                "SECURITY BOUNDARY — The document and output stay inside the selected "
                "workspace. A native tool is not a sandbox; only choose an Aseprite binary "
                "you installed and trust."
            ),
            style="Advisory.TLabel",
            wraplength=1050,
            justify="left",
        ).grid(row=5, column=0, sticky="ew", pady=METRICS["space_md"])
        self.aseprite_results = self._results_text(panel, height=17)
        self.aseprite_results.grid(row=10, column=0, sticky="nsew")

    # --------------------------------------------------------- shared workflow

    def choose_workspace(self) -> None:
        if self.controller.busy:
            self._set_status("working", "Finish the current bounded task first.")
            return
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose a trusted sprite workspace",
            mustexist=True,
        )
        if not selected:
            return
        try:
            root = Path(selected).resolve(strict=True)
            if not root.is_dir():
                raise OSError
        except (OSError, RuntimeError, ValueError):
            self._set_status("error", "The trusted workspace is unavailable.")
            return
        self.controller.advance_generation()
        self._stop_motion_playback()
        self.workspace = root
        self.workspace_text.set(f"TRUSTED WORKSPACE: {root.name}")
        self._reset_documents()
        self._set_status("idle", "Workspace ready. Choose a workflow.")
        self._sync_controls()

    def _reset_documents(self) -> None:
        self._inspect_source = None
        self._palette_source = None
        self._palette_file = None
        self._motion_paths = []
        self._motion_images = []
        self._motion_index = 0
        self._aseprite_document = None
        self.inspect_file_text.set("No PNG selected")
        self.inspect_preview_text.set("Preview: no sprite selected")
        self.palette_file_text.set("No PNG selected")
        self.palette_preview_text.set("Preview: choose a sprite and palette")
        self.palette_source_text.set("Using an original built-in palette")
        self.motion_frame_text.set("Frame 0 of 0")
        self.motion_preview_text.set("Preview: choose two or more ordered PNG frames")
        self.aseprite_document_text.set("No Aseprite document selected")
        self._aseprite_executable = None
        self._aseprite_sha256 = None
        self.aseprite_executable_text.set("No Aseprite executable pinned")
        self._clear_canvas(self.inspect_canvas)
        self._clear_canvas(self.palette_canvas)
        self._clear_canvas(self.motion_canvas)
        self._draw_palette_swatches()
        self.motion_list.delete(0, tk.END)
        for widget in (self.inspect_results, self.motion_results, self.aseprite_results):
            self._set_text(widget, "")

    def _require_workspace(self) -> Path | None:
        if self.workspace is None:
            self._set_status("error", "Choose a trusted workspace first.")
            self.workspace_button.focus_set()
            return None
        return self.workspace

    def _relative_input(self, selected: str, suffixes: tuple[str, ...]) -> str | None:
        root = self._require_workspace()
        if root is None:
            return None
        try:
            path = Path(selected).resolve(strict=True)
            relative = path.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            self._set_status("error", "The selected file is outside the trusted workspace.")
            return None
        if not path.is_file() or path.suffix.lower() not in suffixes:
            self._set_status("error", "Choose a supported file inside the trusted workspace.")
            return None
        return os.fspath(relative)

    def _relative_output(self, selected: str) -> str | None:
        root = self._require_workspace()
        if root is None:
            return None
        candidate = Path(selected)
        try:
            parent = candidate.parent.resolve(strict=True)
            relative_parent = parent.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            self._set_status("error", "The output must stay inside the trusted workspace.")
            return None
        return os.fspath(relative_parent / candidate.name)

    def _relative_aseprite_output(self) -> str | None:
        root = self._require_workspace()
        if root is None:
            return None
        raw = self.aseprite_output.get().strip()
        if not raw:
            self._set_status("error", "Enter a new relative export directory.")
            return None
        requested = Path(raw)
        if requested.is_absolute() or any(part == ".." for part in requested.parts):
            self._set_status("error", "The export directory must be relative to the workspace.")
            return None
        try:
            (root / requested).resolve(strict=False).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            self._set_status("error", "The export directory must stay inside the workspace.")
            return None
        return os.fspath(requested)

    def _submit(
        self,
        working_message: str,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
        unexpected_message: str,
    ) -> None:
        if self._closed:
            return
        try:
            token = self.controller.begin_job()
        except StudioBusyError:
            self._set_status("working", "Finish the current bounded task first.")
            return
        self._set_status("working", working_message)
        self._sync_controls()
        try:
            future = self._executor.submit(work)
        except Exception:
            self.controller.fail_job(token)
            self._set_status("error", unexpected_message)
            self._sync_controls()
            return
        self._pending = (future, token, on_success, unexpected_message)
        try:
            self._poll_after = self.root.after(_POLL_INTERVAL_MS, self._poll_job)
        except Exception:
            self._poll_after = None
            self._finish_job(future, token, on_success, unexpected_message)

    def _poll_job(self) -> None:
        self._poll_after = None
        if self._closed or self._pending is None:
            return
        future, token, on_success, unexpected_message = self._pending
        if not future.done():
            try:
                self._poll_after = self.root.after(_POLL_INTERVAL_MS, self._poll_job)
            except Exception:
                self._finish_job(future, token, on_success, unexpected_message)
            return

        self._finish_job(future, token, on_success, unexpected_message)

    def _finish_job(
        self,
        future: Future[Any],
        token: JobToken,
        on_success: Callable[[Any], None],
        unexpected_message: str,
    ) -> None:
        self._pending = None
        try:
            result = future.result()
        except StudioError as error:
            accepted = self.controller.fail_job(token)
            if accepted:
                self._set_status("error", str(error))
        except Exception:
            accepted = self.controller.fail_job(token)
            if accepted:
                self._set_status("error", unexpected_message)
        else:
            accepted = self.controller.finish_job(token, result)
            if accepted:
                try:
                    on_success(result)
                except Exception:
                    self._set_status("error", unexpected_message)
        self._sync_controls()
        if self._close_requested:
            self._finalize_close()

    def _report_callback_exception(
        self,
        _exception_type: type[BaseException],
        _exception: BaseException,
        _traceback: object,
    ) -> None:
        """Replace Tk's traceback printer with one fixed, path-free UI state."""
        if self._closed:
            return
        try:
            self._set_status("error", "An unexpected interface error occurred.")
        except Exception:
            return

    def _set_status(self, state: str, message: str) -> None:
        label = STATUS_LABELS.get(state, STATUS_LABELS["idle"])
        self.status_text.set(f"STATUS: {label} — {message}")

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _clear_canvas(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        self._photos.pop(canvas, None)
        self._preview_sources.pop(canvas, None)
        self._draw_empty_canvas(canvas)

    def _draw_empty_canvas(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            width, height = _PREVIEW_BOX
        canvas.create_text(
            width // 2,
            height // 2,
            text="NO PREVIEW",
            fill=COLORS["canvas_text"],
            font=FONTS["heading"],
        )

    def _canvas_resized(self, event: tk.Event) -> None:
        canvas = event.widget
        if not isinstance(canvas, tk.Canvas) or self._closed:
            return
        if canvas in self._preview_sources:
            self._render_image(canvas)
        else:
            self._draw_empty_canvas(canvas)

    def _show_image(
        self,
        canvas: tk.Canvas,
        image: Image.Image,
        caption: tk.StringVar,
        label: str,
    ) -> None:
        self._preview_sources[canvas] = (image.copy(), caption, label)
        self._render_image(canvas)

    def _render_image(self, canvas: tk.Canvas) -> None:
        source = self._preview_sources.get(canvas)
        if source is None:
            self._draw_empty_canvas(canvas)
            return
        image, caption, label = source
        width, height = image.size
        max_width = canvas.winfo_width()
        max_height = canvas.winfo_height()
        if max_width <= 1 or max_height <= 1:
            max_width, max_height = _PREVIEW_BOX
        inset = 2 * METRICS["border"] + METRICS["space_sm"]
        available_width = max(1, max_width - inset)
        available_height = max(1, max_height - inset)
        scale = min(
            available_width / max(width, 1),
            available_height / max(height, 1),
        )
        if scale >= 1:
            scale = max(1, int(scale))
        target = (max(1, int(width * scale)), max(1, int(height * scale)))
        rendered = image.convert("RGBA").resize(target, Image.Resampling.NEAREST)
        photo = ImageTk.PhotoImage(rendered, master=self.root)
        self._photos[canvas] = photo
        canvas.delete("all")
        canvas.create_image(max_width // 2, max_height // 2, image=photo, anchor="center")
        caption.set(f"Preview: {label} — {width}×{height}px — nearest-neighbor")

    def _sync_controls(self) -> None:
        busy = self.controller.busy
        workspace = self.workspace is not None

        def enabled(widget: ttk.Widget, condition: bool) -> None:
            widget.state(["!disabled"] if condition else ["disabled"])

        for widget in (
            self.palette_combo,
            self.ramp_base_entry,
            self.ramp_stops_spinbox,
            self.ramp_drift_spinbox,
            self.dither_color_a_entry,
            self.dither_color_b_entry,
            self.dither_size_spinbox,
            self.dither_order_combo,
            self.aseprite_tag_entry,
            self.aseprite_layer_entry,
            self.aseprite_output_entry,
        ):
            enabled(widget, not busy)

        enabled(self.workspace_button, not busy)
        enabled(self.inspect_choose_button, workspace and not busy)
        enabled(self.inspect_run_button, workspace and self._inspect_source is not None and not busy)
        enabled(self.palette_choose_button, workspace and not busy)
        enabled(self.palette_builtin_button, workspace and bool(self.palette_name.get()) and not busy)
        enabled(self.palette_json_button, workspace and not busy)
        palette_ready = (
            workspace
            and self._palette_source is not None
            and (self._palette_file is not None or bool(self.palette_name.get()))
        )
        enabled(self.palette_preview_button, palette_ready and not busy)
        enabled(self.palette_export_button, palette_ready and not busy)
        enabled(self.ramp_preview_button, workspace and not busy)
        enabled(self.ramp_export_button, workspace and not busy)
        enabled(self.dither_preview_button, workspace and not busy)
        enabled(self.dither_export_button, workspace and not busy)
        enabled(self.motion_choose_button, workspace and not busy)
        frames_ready = len(self._motion_images) >= 2
        enabled(self.motion_qa_button, frames_ready and not busy)
        has_frames = bool(self._motion_images)
        for widget in (
            self.motion_first_button,
            self.motion_prev_button,
            self.motion_play_button,
            self.motion_next_button,
            self.motion_last_button,
        ):
            enabled(widget, has_frames and not busy)
        selected = bool(self.motion_list.curselection())
        enabled(self.motion_up_button, selected and not busy)
        enabled(self.motion_down_button, selected and not busy)
        enabled(self.motion_remove_button, selected and not busy)
        enabled(self.aseprite_document_button, workspace and not busy)
        enabled(self.aseprite_executable_button, workspace and not busy)
        aseprite_ready = (
            workspace
            and self._aseprite_document is not None
            and self._aseprite_executable is not None
            and self._aseprite_sha256 is not None
        )
        enabled(self.aseprite_qa_button, aseprite_ready and not busy)
        enabled(self.aseprite_export_button, aseprite_ready and not busy)

    # --------------------------------------------------------------- inspection

    def choose_inspect_png(self) -> None:
        root = self._require_workspace()
        if root is None:
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a PNG to inspect",
            initialdir=root,
            filetypes=_PNG_TYPES,
        )
        if not selected:
            return
        relative = self._relative_input(selected, (".png",))
        if relative is None:
            return
        self.controller.advance_generation()
        self._inspect_source = relative
        self.inspect_file_text.set(relative)
        self._set_text(self.inspect_results, "Ready to inspect this sprite.")
        self._submit(
            "Loading sprite preview…",
            lambda: load_preview(relative, trusted_root=root),
            lambda image: self._inspection_loaded(relative, image),
            "Sprite preview failed.",
        )

    def _inspection_loaded(self, relative: str, image: Image.Image) -> None:
        self._show_image(self.inspect_canvas, image, self.inspect_preview_text, relative)
        self._set_status("idle", "Sprite loaded. Run craft and contrast inspection.")

    def run_inspection(self) -> None:
        root = self._require_workspace()
        source = self._inspect_source
        if root is None or source is None:
            return

        def work() -> tuple[dict, Image.Image]:
            return (
                inspect_sprite(source, trusted_root=root),
                load_preview(source, trusted_root=root),
            )

        self._submit(
            "Inspecting craft and contrast…",
            work,
            lambda result: self._inspection_finished(source, result),
            "Sprite inspection failed.",
        )

    def _inspection_finished(
        self,
        source: str,
        result: tuple[dict, Image.Image],
    ) -> None:
        report, image = result
        self._show_image(self.inspect_canvas, image, self.inspect_preview_text, source)
        self._set_text(self.inspect_results, self._format_inspection(report))
        craft = dict(report.get("critique", {}))
        contrast = dict(report.get("contrast", {}))
        needs_review = bool(craft.get("findings")) or bool(contrast.get("weak"))
        if needs_review:
            self._set_status("review", "Inspection found areas for human review.")
        else:
            self._set_status("pass", "Inspection complete with no automated findings.")

    @staticmethod
    def _format_inspection(report: dict) -> str:
        craft = report.get("critique", {})
        contrast = report.get("contrast", {})
        try:
            score = f"{float(craft.get('score', 0)):.0%}"
        except (TypeError, ValueError):
            score = "unavailable"
        lines = [
            f"FILE: {report.get('file', 'sprite.png')}",
            f"CRAFT SCORE: {score}",
            f"CONTRAST: {contrast.get('summary', 'No summary available')}",
            "",
            "FINDINGS",
        ]
        findings = list(craft.get("findings", []))
        if not findings:
            lines.append("No automated craft findings.")
        for finding in findings:
            check = str(finding.get("check", "finding")).replace("_", " ").upper()
            detail = str(finding.get("detail", "Review this area."))
            lines.append(f"• {check}: {detail}")
            hint = finding.get("hint")
            if hint:
                lines.append(f"  TRY: {hint}")
        hints = list(craft.get("retry_hints", []))
        if hints:
            lines.extend(("", "RETRY HINTS"))
            lines.extend(f"• {hint}" for hint in hints)
        lines.extend(("", "ADVISORY: This report supports human review; it is not approval."))
        return "\n".join(lines)

    # ---------------------------------------------------------------- palette

    def choose_palette_png(self) -> None:
        root = self._require_workspace()
        if root is None:
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a PNG to quantize",
            initialdir=root,
            filetypes=_PNG_TYPES,
        )
        if not selected:
            return
        relative = self._relative_input(selected, (".png",))
        if relative is None:
            return
        self.controller.advance_generation()
        self._palette_source = relative
        self.palette_file_text.set(relative)
        self._submit(
            "Loading palette source…",
            lambda: load_preview(relative, trusted_root=root),
            lambda image: self._palette_source_loaded(relative, image),
            "Palette source could not be loaded.",
        )

    def _palette_source_loaded(self, relative: str, image: Image.Image) -> None:
        self._show_image(self.palette_canvas, image, self.palette_preview_text, relative)
        self._set_status("idle", "Source loaded. Preview the selected palette.")

    def _palette_changed(self, _event: tk.Event | None = None) -> None:
        self._use_builtin_palette()

    def _use_builtin_palette(self) -> None:
        self.controller.advance_generation()
        self._palette_file = None
        self.palette_source_text.set("Using an original built-in palette")
        self._draw_palette_swatches()
        self._sync_controls()

    def choose_palette_json(self) -> None:
        root = self._require_workspace()
        if root is None:
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a user-owned palette JSON inside the workspace",
            initialdir=root,
            filetypes=_PALETTE_TYPES,
        )
        if not selected:
            return
        relative = self._relative_input(selected, (".json",))
        if relative is None:
            return
        self.controller.advance_generation()
        self._submit(
            "Validating bounded user palette…",
            lambda: load_user_palette(relative, trusted_root=root),
            lambda result: self._user_palette_loaded(relative, result),
            "User palette could not be loaded.",
        )

    def _user_palette_loaded(self, relative: str, result: dict) -> None:
        self._palette_file = relative
        self._draw_swatches(self.palette_swatches, list(result.get("colors", [])))
        metadata = dict(result.get("metadata", {}))
        name = str(metadata.get("name", "User palette"))
        self.palette_source_text.set(f"User palette: {Path(relative).name}")
        self.palette_info_text.set(
            f"{len(result.get('colors', []))} colors — {name}; verify provenance"
        )
        self._set_status("idle", "User-owned palette validated inside the workspace.")
        self._sync_controls()

    def _draw_palette_swatches(self) -> None:
        self.palette_swatches.delete("all")
        name = self.palette_name.get()
        if not name:
            self.palette_info_text.set("No built-in palettes available")
            return
        try:
            colors, metadata = load_palette(name)
        except Exception:
            self.palette_info_text.set("Palette metadata is unavailable")
            return
        self._draw_swatches(self.palette_swatches, colors)
        author = metadata.get("author", "LenkRaster")
        self.palette_info_text.set(f"{len(colors)} colors — original palette by {author}")

    def _palette_context(self) -> tuple[str | None, str | None, str] | None:
        if self._palette_file is not None:
            return None, self._palette_file, Path(self._palette_file).stem
        palette = self.palette_name.get()
        if not palette:
            self._set_status("error", "Choose a built-in or user-owned palette.")
            return None
        return palette, None, palette

    @staticmethod
    def _draw_swatches(canvas: tk.Canvas, colors: list[str] | tuple[str, ...]) -> None:
        canvas.delete("all")
        count = max(len(colors), 1)
        width = canvas.winfo_width()
        if width <= 1:
            width = 480
        swatch = min(42.0, max(1.0, (width - 8) / count))
        height = max(canvas.winfo_height(), int(canvas.cget("height")))
        for index, color in enumerate(colors):
            x0 = 4 + index * swatch
            canvas.create_rectangle(
                x0,
                4,
                x0 + swatch,
                max(8, height - 4),
                fill=color,
                outline=COLORS["text"],
                width=1,
            )

    def run_palette_preview(self) -> None:
        root = self._require_workspace()
        source = self._palette_source
        context = self._palette_context()
        if root is None or source is None or context is None:
            return
        palette, palette_file, _label = context
        self._submit(
            "Building in-memory quantized preview…",
            lambda: preview_quantization(
                source,
                palette,
                trusted_root=root,
                palette_file=palette_file,
            ),
            lambda result: self._palette_preview_finished(source, result),
            "Palette preview failed.",
        )

    def _palette_preview_finished(self, source: str, result: dict) -> None:
        self._show_image(self.palette_canvas, result["image"], self.palette_preview_text, source)
        used = ", ".join(result.get("used_colors", []))
        self.palette_info_text.set(
            f"{result.get('colors_used', 0)} colors used"
            + (f" — {used}" if used else "")
        )
        self._set_status("idle", "Palette preview ready; no file was written.")

    def export_palette_copy(self) -> None:
        root = self._require_workspace()
        source = self._palette_source
        context = self._palette_context()
        if root is None or source is None or context is None:
            return
        palette, palette_file, label = context
        default = f"{Path(source).stem}.{label}.png"
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export a new quantized PNG",
            initialdir=root,
            initialfile=default,
            defaultextension=".png",
            filetypes=_PNG_TYPES,
            confirmoverwrite=False,
        )
        if not selected:
            return
        output = self._relative_output(selected)
        if output is None:
            return
        self._submit(
            "Publishing a create-only quantized PNG…",
            lambda: export_quantized_png(
                source,
                palette,
                output,
                trusted_root=root,
                palette_file=palette_file,
            ),
            lambda result: self._export_finished(result, "Quantized PNG"),
            "Quantized export failed.",
        )

    def _ramp_values(self) -> tuple[str, int, float] | None:
        try:
            return self.ramp_base.get().strip(), int(self.ramp_stops.get()), float(self.ramp_drift.get())
        except (tk.TclError, TypeError, ValueError):
            self._set_status("error", "Enter a valid hex color, stop count, and hue drift.")
            return None

    def run_ramp_preview(self) -> None:
        if self._require_workspace() is None:
            return
        values = self._ramp_values()
        if values is None:
            return
        base, stops, drift = values
        self._submit(
            "Building original material ramp…",
            lambda: preview_ramp(base, stops=stops, drift=drift),
            self._ramp_preview_finished,
            "Color ramp is invalid.",
        )

    def _ramp_preview_finished(self, result: dict) -> None:
        colors = list(result.get("colors", []))
        self._draw_swatches(self.ramp_swatches, colors)
        self.ramp_info_text.set(" → ".join(colors))
        self._show_image(
            self.palette_canvas,
            result["image"],
            self.palette_preview_text,
            "material ramp",
        )
        self._set_status("idle", "Original material ramp ready.")

    def export_ramp_copy(self) -> None:
        root = self._require_workspace()
        values = self._ramp_values()
        if root is None or values is None:
            return
        base, stops, drift = values
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export a new ramp PNG",
            initialdir=root,
            initialfile="lenkraster-ramp.png",
            defaultextension=".png",
            filetypes=_PNG_TYPES,
            confirmoverwrite=False,
        )
        if not selected:
            return
        output = self._relative_output(selected)
        if output is None:
            return
        self._submit(
            "Publishing a create-only ramp PNG…",
            lambda: export_ramp_png(
                base,
                output,
                trusted_root=root,
                stops=stops,
                drift=drift,
            ),
            lambda result: self._ramp_export_finished(result),
            "Ramp export failed.",
        )

    def _ramp_export_finished(self, result: dict) -> None:
        self._draw_swatches(self.ramp_swatches, list(result.get("colors", [])))
        self.ramp_info_text.set(" → ".join(result.get("colors", [])))
        self._export_finished(result, "Ramp PNG")

    def _dither_values(self) -> tuple[str, str, int, int] | None:
        try:
            return (
                self.dither_color_a.get().strip(),
                self.dither_color_b.get().strip(),
                int(self.dither_size.get()),
                int(self.dither_order.get()),
            )
        except (tk.TclError, TypeError, ValueError):
            self._set_status("error", "Enter two valid hex colors, a size, and an order.")
            return None

    def run_dither_preview(self) -> None:
        if self._require_workspace() is None:
            return
        values = self._dither_values()
        if values is None:
            return
        color_a, color_b, size, order = values
        self._submit(
            "Building bounded ordered-dither preview…",
            lambda: preview_dither(
                color_a,
                color_b,
                size=size,
                order=order,
            ),
            self._dither_preview_finished,
            "Dither preview failed.",
        )

    def _dither_preview_finished(self, result: dict) -> None:
        width, height = result.get("size", (0, 0))
        order = result.get("order", "?")
        self._show_image(
            self.palette_canvas,
            result["image"],
            self.palette_preview_text,
            f"ordered {order}×{order} dither",
        )
        self.dither_info_text.set(
            f"{width}×{height}px — {' + '.join(result.get('colors', []))}"
        )
        self._set_status("idle", "Dither preview ready; no file was written.")

    def export_dither_copy(self) -> None:
        root = self._require_workspace()
        values = self._dither_values()
        if root is None or values is None:
            return
        color_a, color_b, size, order = values
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export a new ordered-dither PNG",
            initialdir=root,
            initialfile="lenkraster-dither.png",
            defaultextension=".png",
            filetypes=_PNG_TYPES,
            confirmoverwrite=False,
        )
        if not selected:
            return
        output = self._relative_output(selected)
        if output is None:
            return
        self._submit(
            "Publishing a create-only ordered-dither PNG…",
            lambda: export_dither_png(
                color_a,
                color_b,
                output,
                trusted_root=root,
                size=size,
                order=order,
            ),
            lambda result: self._dither_export_finished(result),
            "Dither export failed.",
        )

    def _dither_export_finished(self, result: dict) -> None:
        width, height = result.get("size", (0, 0))
        self.dither_info_text.set(
            f"{width}×{height}px — {' + '.join(result.get('colors', []))}"
        )
        self._export_finished(result, "Dither PNG")

    def _export_finished(self, result: dict, label: str) -> None:
        self._set_status("idle", f"{label} created: {result.get('file', 'new file')}")

    # ----------------------------------------------------------------- motion

    def choose_motion_frames(self) -> None:
        root = self._require_workspace()
        if root is None:
            return
        selected = filedialog.askopenfilenames(
            parent=self.root,
            title="Choose ordered PNG frames",
            initialdir=root,
            filetypes=_PNG_TYPES,
        )
        if not selected:
            return
        if not 2 <= len(selected) <= MAX_FRAMES:
            self._set_status("error", "Choose between 2 and 32 animation frames.")
            return
        relative: list[str] = []
        for path in selected:
            value = self._relative_input(path, (".png",))
            if value is None:
                return
            relative.append(value)
        if len(set(relative)) != len(relative):
            self._set_status("error", "Choose each animation frame once.")
            return
        self.controller.advance_generation()
        self._stop_motion_playback()
        self._motion_paths = relative
        self._motion_images = []
        self.motion_list.delete(0, tk.END)
        for index, path in enumerate(relative, start=1):
            self.motion_list.insert(tk.END, f"{index:02d}  {path}")
        self.motion_list.selection_set(0)
        self._submit(
            "Loading ordered animation frames…",
            lambda: self._load_motion_images(relative, root),
            self._motion_frames_loaded,
            "Animation frames could not be loaded.",
        )

    @staticmethod
    def _load_motion_images(paths: list[str], root: Path) -> list[Image.Image]:
        images: list[Image.Image] = []
        total_pixels = 0
        for path in paths:
            image = load_preview(path, trusted_root=root)
            total_pixels += image.width * image.height
            if total_pixels > MAX_TOTAL_PIXELS:
                raise StudioError("animation exceeds safety limits")
            images.append(image)
        return images

    def _motion_frames_loaded(self, images: list[Image.Image]) -> None:
        self._motion_images = images
        self._motion_index = 0
        self._show_motion_frame()
        self._set_status("idle", "Frames loaded in the listed order. Reorder or run QA.")

    def _show_motion_frame(self) -> None:
        if not self._motion_images:
            return
        self._motion_index %= len(self._motion_images)
        path = self._motion_paths[self._motion_index]
        self._show_image(
            self.motion_canvas,
            self._motion_images[self._motion_index],
            self.motion_preview_text,
            path,
        )
        self.motion_frame_text.set(
            f"Frame {self._motion_index + 1} of {len(self._motion_images)} — {path}"
        )
        self.motion_list.selection_clear(0, tk.END)
        self.motion_list.selection_set(self._motion_index)
        self.motion_list.see(self._motion_index)
        self._sync_controls()

    def _motion_list_selected(self, _event: tk.Event | None = None) -> None:
        selected = self.motion_list.curselection()
        if selected and self._motion_images:
            self._motion_index = int(selected[0])
            self._show_motion_frame()
        self._sync_controls()

    def motion_first(self) -> None:
        if self._motion_images:
            self._motion_index = 0
            self._show_motion_frame()

    def motion_previous(self) -> None:
        if self._motion_images:
            self._motion_index = (self._motion_index - 1) % len(self._motion_images)
            self._show_motion_frame()

    def motion_next(self) -> None:
        if self._motion_images:
            self._motion_index = (self._motion_index + 1) % len(self._motion_images)
            self._show_motion_frame()

    def motion_last(self) -> None:
        if self._motion_images:
            self._motion_index = len(self._motion_images) - 1
            self._show_motion_frame()

    def toggle_motion_playback(self) -> None:
        if self._play_after is not None:
            self._stop_motion_playback()
            self._set_status("idle", "Animation playback paused.")
            return
        if len(self._motion_images) < 2:
            self._set_status("error", "Choose at least two animation frames.")
            return
        self.motion_play_button.configure(text="Pause")
        self._set_status("idle", "Playing ordered frames; press Pause or Escape to stop.")
        self._play_after = self.root.after(_MOTION_INTERVAL_MS, self._advance_motion_playback)

    def _advance_motion_playback(self) -> None:
        self._play_after = None
        if self._closed or len(self._motion_images) < 2:
            return
        self._motion_index = (self._motion_index + 1) % len(self._motion_images)
        self._show_motion_frame()
        self._play_after = self.root.after(_MOTION_INTERVAL_MS, self._advance_motion_playback)

    def _stop_motion_playback(self) -> None:
        if self._play_after is not None:
            try:
                self.root.after_cancel(self._play_after)
            except tk.TclError:
                pass
            self._play_after = None
        if hasattr(self, "motion_play_button"):
            self.motion_play_button.configure(text="Play")

    def _move_motion_frame(self, delta: int) -> None:
        selected = self.motion_list.curselection()
        if not selected or not self._motion_images:
            return
        index = int(selected[0])
        target = index + delta
        if target < 0 or target >= len(self._motion_paths):
            return
        self._stop_motion_playback()
        self._motion_paths[index], self._motion_paths[target] = (
            self._motion_paths[target],
            self._motion_paths[index],
        )
        self._motion_images[index], self._motion_images[target] = (
            self._motion_images[target],
            self._motion_images[index],
        )
        self._motion_index = target
        self._refresh_motion_list()
        self._show_motion_frame()
        self._set_status("idle", "Frame order updated; run QA again.")

    def _remove_motion_frame(self) -> None:
        selected = self.motion_list.curselection()
        if not selected:
            return
        self._stop_motion_playback()
        index = int(selected[0])
        del self._motion_paths[index]
        if index < len(self._motion_images):
            del self._motion_images[index]
        self._motion_index = min(index, max(len(self._motion_images) - 1, 0))
        self._refresh_motion_list()
        if self._motion_images:
            self._show_motion_frame()
        else:
            self._clear_canvas(self.motion_canvas)
            self.motion_frame_text.set("Frame 0 of 0")
        self._set_status("idle", "Frame removed; at least two are required for QA.")
        self._sync_controls()

    def _refresh_motion_list(self) -> None:
        self.motion_list.delete(0, tk.END)
        for index, path in enumerate(self._motion_paths, start=1):
            self.motion_list.insert(tk.END, f"{index:02d}  {path}")
        if self._motion_paths:
            self.motion_list.selection_set(self._motion_index)

    def run_motion_qa(self) -> None:
        root = self._require_workspace()
        if root is None or len(self._motion_paths) < 2:
            self._set_status("error", "Choose at least two animation frames.")
            return
        paths = list(self._motion_paths)
        self._submit(
            "Running bounded animation-cycle QA…",
            lambda: analyze_cycle(paths, trusted_root=root),
            self._motion_qa_finished,
            "Animation analysis failed.",
        )

    def _motion_qa_finished(self, report: dict) -> None:
        verdict = str(report.get("verdict", "REVIEW")).upper()
        lines = [
            f"VERDICT: {verdict}",
            f"FRAMES: {len(report.get('frame_names', []))}",
            f"MOTION PEAK: {report.get('motion_peak_mad', 'n/a')}",
            f"CHANGED PIXELS: {report.get('motion_peak_pixels', 'n/a')}",
            "",
            "ISSUES",
        ]
        issues = list(report.get("issues", []))
        lines.extend((f"• {issue}" for issue in issues) if issues else ["No automated cycle issues."])
        hints = list(report.get("hints", []))
        if hints:
            lines.extend(("", "HINTS"))
            lines.extend(f"• {hint}" for hint in hints)
        lines.extend(("", "ADVISORY: Cycle QA is evidence for human review, not approval."))
        self._set_text(self.motion_results, "\n".join(lines))
        state = "pass" if verdict == "PASS" else "review"
        self._set_status(state, f"Cycle QA returned {verdict}.")

    # --------------------------------------------------------------- Aseprite

    def choose_aseprite_document(self) -> None:
        root = self._require_workspace()
        if root is None:
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Choose an Aseprite document inside the workspace",
            initialdir=root,
            filetypes=_ASEPRITE_TYPES,
        )
        if not selected:
            return
        relative = self._relative_input(selected, (".ase", ".aseprite"))
        if relative is None:
            return
        self.controller.advance_generation()
        self._aseprite_document = relative
        self.aseprite_document_text.set(relative)
        stem = Path(relative).stem
        self.aseprite_output.set(f"exports/{stem}")
        self._set_status("idle", "Aseprite document selected inside the workspace.")
        self._sync_controls()

    def choose_aseprite_executable(self) -> None:
        if self._require_workspace() is None:
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Choose the Aseprite executable you trust",
            filetypes=_EXECUTABLE_TYPES,
        )
        if not selected:
            return
        path = Path(selected)
        self.controller.advance_generation()
        self._aseprite_executable = None
        self._aseprite_sha256 = None
        self.aseprite_executable_text.set("Pinning selected executable…")
        self._submit(
            "Computing Aseprite SHA-256 pin…",
            lambda: self._hash_executable(path),
            lambda digest: self._aseprite_pinned(path, digest),
            "Aseprite executable could not be pinned.",
        )

    @staticmethod
    def _hash_executable(path: Path) -> str:
        try:
            if not path.is_file():
                raise OSError
            size = path.stat().st_size
            if size <= 0 or size > _MAX_EXECUTABLE_BYTES:
                raise OSError
            digest = hashlib.sha256()
            total = 0
            with path.open("rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > _MAX_EXECUTABLE_BYTES:
                        raise OSError
                    digest.update(block)
            return digest.hexdigest()
        except OSError:
            raise StudioError("Aseprite executable could not be pinned") from None

    def _aseprite_pinned(self, path: Path, digest: str) -> None:
        self._aseprite_executable = path
        self._aseprite_sha256 = digest
        self.aseprite_executable_text.set(f"{path.name} — SHA-256 {digest[:16]}… pinned")
        self._set_status("idle", "Aseprite executable pinned for explicit local use.")

    def _aseprite_context(self) -> tuple[Path, str, Path, str, str | None, str | None] | None:
        root = self._require_workspace()
        if (
            root is None
            or self._aseprite_document is None
            or self._aseprite_executable is None
            or self._aseprite_sha256 is None
        ):
            self._set_status("error", "Choose a workspace document and pin Aseprite first.")
            return None
        tag = self.aseprite_tag.get().strip() or None
        layer = self.aseprite_layer.get().strip() or None
        return (
            root,
            self._aseprite_document,
            self._aseprite_executable,
            self._aseprite_sha256,
            tag,
            layer,
        )

    def run_aseprite_qa(self) -> None:
        context = self._aseprite_context()
        if context is None:
            return
        root, document, executable, digest, tag, layer = context
        self._submit(
            "Running pinned Aseprite export and bounded cycle QA…",
            lambda: qa_aseprite(
                document,
                trusted_root=root,
                executable=executable,
                executable_sha256=digest,
                tag=tag,
                layer=layer,
            ),
            self._aseprite_qa_finished,
            "Aseprite analysis failed.",
        )

    def _aseprite_qa_finished(self, report: dict) -> None:
        self._set_text(self.aseprite_results, self._safe_json(report))
        verdict = str(report.get("verdict", "REVIEW")).upper()
        state = "pass" if verdict == "PASS" else "review"
        self._set_status(state, f"Aseprite cycle QA returned {verdict}.")

    def run_aseprite_export(self) -> None:
        context = self._aseprite_context()
        output = self._relative_aseprite_output()
        if context is None or output is None:
            return
        root, document, executable, digest, tag, layer = context
        self._submit(
            "Running pinned, create-only Aseprite export…",
            lambda: export_aseprite(
                document,
                output,
                trusted_root=root,
                executable=executable,
                executable_sha256=digest,
                tag=tag,
                layer=layer,
            ),
            lambda report: self._aseprite_export_finished(output, report),
            "Aseprite export failed.",
        )

    def _aseprite_export_finished(self, output: str, report: dict) -> None:
        self._set_text(self.aseprite_results, self._safe_json(report))
        self._set_status("idle", f"Aseprite export created: {output}")

    @staticmethod
    def _safe_json(value: Any) -> str:
        try:
            rendered = json.dumps(value, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            return "Result is available but could not be displayed."
        if len(rendered) > 32_768:
            return rendered[:32_768] + "\n… display truncated …"
        return rendered

    # ---------------------------------------------------------------- lifecycle

    def _on_escape(self, _event: tk.Event | None = None) -> str:
        self._stop_motion_playback()
        if self.controller.busy:
            self._set_status("working", "Playback stopped; the bounded task is still running.")
        else:
            self._set_status("idle", "Playback stopped.")
        return "break"

    def close(self) -> None:
        if self._closed:
            return
        if self._pending is not None and not self._pending[0].done():
            self._close_requested = True
            self._stop_motion_playback()
            self._set_status("working", "Finishing the active bounded task before closing.")
            if self._poll_after is None:
                self._poll_after = self.root.after(_POLL_INTERVAL_MS, self._poll_job)
            return
        self._finalize_close()

    def _finalize_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_motion_playback()
        if self._poll_after is not None:
            try:
                self.root.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        if self._pending is not None:
            self._pending[0].cancel()
            self._pending = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        try:
            self.root.destroy()
        except tk.TclError:
            pass


__all__ = ["StudioWindow"]
