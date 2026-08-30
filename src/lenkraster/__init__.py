"""LenkRaster: bounded pixel-art critique, palette, cycle, and Aseprite tools."""
from .aseprite import export_document as export_aseprite_document
from .aseprite import qa_document as qa_aseprite_document
from .critic import critique, critique_many
from .cycle import qa_cycle
from .palette import (available_palettes, check_contrast, dither_image, load_palette,
                      load_palette_file, make_ramp, quantize_file)

__version__ = "0.1.1"
__all__ = [
    "critique",
    "critique_many",
    "qa_cycle",
    "make_ramp",
    "load_palette",
    "load_palette_file",
    "available_palettes",
    "quantize_file",
    "check_contrast",
    "dither_image",
    "export_aseprite_document",
    "qa_aseprite_document",
]
