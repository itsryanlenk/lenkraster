"""Real-Tk layout regression checks for LenkRaster Studio.

The module also runs under ``unittest`` so maintainers can exercise it with any local
Python build that provides Tk, even when the project's test environment is headless.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import tkinter as tk
from tkinter import ttk
import unittest

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lenkraster.studio.view import StudioWindow  # noqa: E402
from lenkraster.studio.theme import COLORS, METRICS  # noqa: E402


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


class StudioLayoutTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {type(error).__name__}")
        self.window = StudioWindow(self.root)

    def tearDown(self):
        if hasattr(self, "window"):
            self.window.close()

    def test_palette_actions_remain_visible_at_the_declared_minimum_size(self):
        self.root.geometry("960x640")
        self.window.notebook.select(self.window.palette_tab)
        self.root.update()

        palette_bottom = (
            self.window.palette_tab.winfo_rooty()
            + self.window.palette_tab.winfo_height()
        )
        for _name, (panel, actions) in self.window.palette_mode_actions.items():
            self.window.palette_modes.select(panel)
            self.root.update()
            for action in actions:
                self.assertTrue(action.winfo_ismapped(), action.cget("text"))
                action_bottom = action.winfo_rooty() + action.winfo_height()
                self.assertLessEqual(
                    action_bottom,
                    palette_bottom,
                    f"{action.cget('text')} is clipped at the supported minimum size",
                )

    def test_previews_fit_the_actual_canvas_after_window_shrink(self):
        self.root.geometry("960x640")
        source = Image.new("RGBA", (16, 16), (95, 193, 255, 255))
        canvases = (
            (self.window.inspect_tab, self.window.inspect_canvas, self.window.inspect_preview_text),
            (self.window.palette_tab, self.window.palette_canvas, self.window.palette_preview_text),
            (self.window.motion_tab, self.window.motion_canvas, self.window.motion_preview_text),
        )

        for tab, canvas, caption in canvases:
            self.window.notebook.select(tab)
            self.root.update()
            self.window._show_image(canvas, source, caption, "test sprite")
            self.root.update()
            left, top, right, bottom = canvas.bbox("all")
            self.assertGreaterEqual(left, 0)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(right, canvas.winfo_width())
            self.assertLessEqual(bottom, canvas.winfo_height())

    def test_maximum_size_user_palette_keeps_every_swatch_visible(self):
        self.root.geometry("960x640")
        self.window.notebook.select(self.window.palette_tab)
        self.window.palette_modes.select(self.window.palette_snap_panel)
        self.root.update()
        colors = [f"#{index:02x}{(255 - index):02x}80" for index in range(64)]

        self.window._draw_swatches(self.window.palette_swatches, colors)
        self.root.update()

        left, _top, right, _bottom = self.window.palette_swatches.bbox("all")
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, self.window.palette_swatches.winfo_width())

    def test_job_parameters_are_disabled_while_their_result_is_pending(self):
        parameter_widgets = (
            self.window.palette_combo,
            self.window.ramp_base_entry,
            self.window.ramp_stops_spinbox,
            self.window.ramp_drift_spinbox,
            self.window.dither_color_a_entry,
            self.window.dither_color_b_entry,
            self.window.dither_size_spinbox,
            self.window.dither_order_combo,
            self.window.aseprite_tag_entry,
            self.window.aseprite_layer_entry,
            self.window.aseprite_output_entry,
        )
        token = self.window.controller.begin_job()

        self.window._sync_controls()

        for widget in parameter_widgets:
            self.assertTrue(widget.instate(["disabled"]), str(widget))

        self.window.controller.fail_job(token)
        self.window._sync_controls()
        for widget in parameter_widgets:
            self.assertTrue(widget.instate(["!disabled"]), str(widget))
        self.assertTrue(self.window.palette_combo.instate(["readonly"]))
        self.assertTrue(self.window.dither_order_combo.instate(["readonly"]))

    def test_buttons_have_hard_offset_depth_and_pressed_feedback(self):
        self.root.update()
        self.assertTrue(
            all(
                isinstance(button, ttk.Button)
                for button in (
                    self.window.workspace_button,
                    self.window.inspect_choose_button,
                    self.window.inspect_run_button,
                    self.window.palette_preview_button,
                    self.window.motion_qa_button,
                    self.window.aseprite_export_button,
                )
            )
        )
        style = ttk.Style(self.root)
        layout = style.layout("TButton")
        self.assertEqual(layout[0][0], "LenkRaster.Button.shadow")
        header_layout = style.layout("HeaderPrimary.TButton")
        self.assertEqual(header_layout[0][0], "LenkRaster.HeaderButton.shadow")
        self.assertGreaterEqual(METRICS["button_depth"], 4)
        self.assertEqual(str(style.lookup("TButton", "relief")), "solid")
        self.assertEqual(style.lookup("TButton", "focuscolor"), COLORS["canvas"])
        self.assertEqual(style.lookup("TButton", "background"), COLORS["surface"])
        self.assertEqual(
            style.lookup("Primary.TButton", "background"),
            COLORS["accent"],
        )
        self.assertEqual(
            style.lookup("HeaderPrimary.TButton", "background"),
            COLORS["accent"],
        )
        self.assertEqual(
            str(self.window.workspace_button.cget("style")),
            "HeaderPrimary.TButton",
        )

        normal, pressed, disabled = self.window._button_shadow_images
        header_normal, header_pressed, header_disabled = (
            self.window._header_button_shadow_images
        )
        center = METRICS["button_depth"]
        surface = _rgb(COLORS["surface"])
        background = _rgb(COLORS["background"])
        shadow = _rgb(COLORS["shadow"])
        muted_shadow = _rgb(COLORS["disabled_shadow"])
        for image in (
            normal,
            pressed,
            disabled,
            header_normal,
            header_pressed,
            header_disabled,
        ):
            self.assertTrue(image.transparency_get(center, center))
            self.assertFalse(image.transparency_get(0, center + 1))
            self.assertFalse(image.transparency_get(center + 1, 0))
        self.assertEqual(normal.get(0, center + 1), surface)
        self.assertEqual(normal.get(center + 1, 0), surface)
        self.assertEqual(normal.get(center + 1, center), shadow)
        self.assertEqual(normal.get(center, center + 1), shadow)
        self.assertEqual(pressed.get(0, center + 1), surface)
        self.assertEqual(pressed.get(center + 1, 0), surface)
        self.assertTrue(pressed.transparency_get(center + 1, center))
        self.assertTrue(pressed.transparency_get(center, center + 1))
        self.assertEqual(disabled.get(center + 1, center), muted_shadow)
        self.assertEqual(disabled.get(center, center + 1), muted_shadow)
        self.assertEqual(header_normal.get(0, center + 1), background)
        self.assertEqual(header_normal.get(center + 1, 0), background)
        self.assertEqual(header_normal.get(center + 1, center), shadow)
        self.assertEqual(header_normal.get(center, center + 1), shadow)
        self.assertEqual(header_pressed.get(0, center + 1), background)
        self.assertEqual(header_pressed.get(center + 1, 0), background)
        self.assertTrue(header_pressed.transparency_get(center + 1, center))
        self.assertEqual(header_disabled.get(center + 1, center), muted_shadow)

        background_states = dict(style.map("TButton", "background"))
        self.assertEqual(background_states["disabled"], COLORS["disabled_surface"])
        self.assertEqual(background_states["pressed"], COLORS["focus"])
        self.assertEqual(background_states["active"], COLORS["accent"])

        invocations = []
        probe = ttk.Button(
            self.root,
            text="Depth probe",
            command=lambda: invocations.append(1),
        )
        probe.state(["disabled"])
        probe.invoke()
        self.assertEqual(invocations, [])
        probe.state(["!disabled"])
        probe.invoke()
        self.assertEqual(invocations, [1])

        self.window.workspace_button.focus_force()
        self.root.update()
        self.assertTrue(self.window.workspace_button.instate(["focus"]))

        self.window._configure_styles()
        self.assertEqual(
            ttk.Style(self.root).layout("TButton")[0][0],
            "LenkRaster.Button.shadow",
        )

    def test_close_cancels_idle_callbacks_before_a_second_root_is_created(self):
        """A closed root must not leave Tcl commands queued for the next root."""
        script = textwrap.dedent(
            """
            import tkinter as tk

            from lenkraster.studio.view import StudioWindow

            first_root = tk.Tk()
            first_root.withdraw()
            first_window = StudioWindow(first_root)
            first_window.close()

            second_root = tk.Tk()
            second_root.withdraw()
            second_window = StudioWindow(second_root)
            second_root.update_idletasks()
            second_window.close()
            """
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE_ROOT)

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("invalid command name", completed.stderr)
        self.assertNotIn('(\"after\" script)', completed.stderr)


if __name__ == "__main__":
    unittest.main()
