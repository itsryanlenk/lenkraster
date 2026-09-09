"""Real-Tk layout regression checks for LenkRaster Studio.

The module also runs under ``unittest`` so maintainers can exercise it with any local
Python build that provides Tk, even when the project's test environment is headless.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tkinter as tk
import unittest

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lenkraster.studio.view import StudioWindow  # noqa: E402


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
        if hasattr(self, "root"):
            try:
                self.root.update()
            except tk.TclError:
                pass

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


if __name__ == "__main__":
    unittest.main()
