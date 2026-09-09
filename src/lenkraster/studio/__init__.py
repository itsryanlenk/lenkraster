"""Trusted, headless application services for LenkRaster Studio.

The desktop interface is deliberately separated from these operations. Importing this
package never imports Tk, and every file operation is rooted in an explicit local
workspace with bounded input and create-only output publication.
"""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile
from typing import Sequence

import numpy as np
from PIL import Image, UnidentifiedImageError

from ..aseprite import (
    AsepriteError,
    export_document as export_aseprite_document,
    qa_document as qa_aseprite_document,
)
from ..critic import critique
from ..cycle import qa_cycle
from ..palette import (
    available_palettes,
    check_contrast,
    dither_image,
    hex2rgb,
    load_palette,
    load_palette_file,
    make_ramp,
    quantize_file,
    rgb2hex,
)


MAX_PATH_CHARS = 1024
MAX_ENCODED_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_SIDE = 2048
MAX_IMAGE_PIXELS = 1_048_576
MAX_FRAMES = 32
MAX_TOTAL_PIXELS = 8_388_608
MAX_PAIR_PIXELS = 67_108_864

_SAFE_ASEPRITE_ERRORS = frozenset({
    "Aseprite cycle requires at least two frames",
    "Aseprite document exceeds safety limits",
    "Aseprite document is outside trusted root",
    "Aseprite document is unavailable",
    "Aseprite executable verification failed",
    "Aseprite export failed",
    "Aseprite input must be an .ase or .aseprite file",
    "Aseprite integration is unavailable",
    "Aseprite output already exists",
    "Aseprite output could not be written",
    "Aseprite output is outside trusted root",
    "Aseprite output is unavailable",
    "Aseprite selection is invalid",
    "Aseprite trusted root is unavailable",
    "Aseprite version is unsupported",
})


class StudioError(ValueError):
    """A fixed, path-free rejection safe to show in the desktop interface."""


def _fail(message: str) -> None:
    raise StudioError(message)


def _pixel_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("image pixel limit is invalid")
    return min(value, MAX_IMAGE_PIXELS)


def _workspace(raw: str | os.PathLike[str]) -> Path:
    try:
        value = os.fspath(raw)
    except TypeError:
        _fail("trusted workspace is unavailable")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_CHARS
        or "\x00" in value
    ):
        _fail("trusted workspace is unavailable")
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("trusted workspace is unavailable")
    if not root.is_dir():
        _fail("trusted workspace is unavailable")
    return root


def _path_value(raw: str | os.PathLike[str]) -> str:
    try:
        value = os.fspath(raw)
    except TypeError:
        _fail("file is unavailable")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_CHARS
        or "\x00" in value
    ):
        _fail("file is unavailable")
    return value


def _within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_input(
        raw: str | os.PathLike[str],
        root: Path,
        *,
        suffixes: tuple[str, ...] | None = None) -> Path:
    value = _path_value(raw)
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("file is unavailable")
    if not _within(root, resolved):
        _fail("file is outside the trusted workspace")
    if not resolved.is_file():
        _fail("file is unavailable")
    if suffixes is not None and resolved.suffix.lower() not in suffixes:
        _fail("file type is unsupported")
    return resolved


def _resolve_output_png(raw: str | os.PathLike[str], root: Path) -> Path:
    value = _path_value(raw)
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    try:
        parent = candidate.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("output directory is unavailable")
    if not _within(root, parent):
        _fail("file is outside the trusted workspace")
    if not parent.is_dir() or candidate.name in ("", ".", ".."):
        _fail("output directory is unavailable")
    output = parent / candidate.name
    if output.suffix.lower() != ".png":
        _fail("output must be a PNG file")
    if os.path.lexists(output):
        _fail("output already exists")
    return output


def _revalidate_output_parent(output: Path, root: Path) -> None:
    try:
        parent = output.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("output directory is unavailable")
    if parent != output.parent or not _within(root, parent) or not parent.is_dir():
        _fail("file is outside the trusted workspace")


def _validate_aseprite_output(raw: str | os.PathLike[str], root: Path) -> None:
    value = _path_value(raw)
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        _fail("output directory is unavailable")
    if not _within(root, resolved):
        _fail("file is outside the trusted workspace")


def _read_png(path: Path, *, max_pixels: int) -> Image.Image:
    max_pixels = _pixel_limit(max_pixels)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_ENCODED_IMAGE_BYTES + 1)
        if not raw or len(raw) > MAX_ENCODED_IMAGE_BYTES:
            _fail("image exceeds safety limits")
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG":
                _fail("input must be a PNG file")
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or width > MAX_IMAGE_SIDE
                or height > MAX_IMAGE_SIDE
                or width * height > max_pixels
            ):
                _fail("image exceeds safety limits")
            image.load()
            return image.convert("RGBA")
    except StudioError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        _fail("input is not a valid PNG")


def _validated_png(
        raw: str | os.PathLike[str],
        root: Path,
        *,
        max_pixels: int) -> tuple[Path, Image.Image]:
    path = _resolve_input(raw, root, suffixes=(".png",))
    return path, _read_png(path, max_pixels=max_pixels)


def _validate_staged_png(path: Path, *, max_pixels: int) -> None:
    _read_png(path, max_pixels=max_pixels)


def _publish_create_only(staged: Path, output: Path) -> None:
    created = False
    try:
        with staged.open("rb") as reader, output.open("xb") as writer:
            created = True
            shutil.copyfileobj(reader, writer, length=64 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    except FileExistsError:
        _fail("output already exists")
    except OSError:
        if created:
            try:
                output.unlink()
            except OSError:
                pass
        _fail("output could not be written")


def _safe_name(path: Path) -> str:
    return path.name


def inspect_sprite(
        image: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str],
        max_pixels: int = MAX_IMAGE_PIXELS) -> dict:
    """Return bounded critique and contrast evidence for one trusted PNG."""
    max_pixels = _pixel_limit(max_pixels)
    root = _workspace(trusted_root)
    path, _preview = _validated_png(image, root, max_pixels=max_pixels)
    try:
        craft = dict(critique(str(path), max_pixels=max_pixels))
        contrast = dict(check_contrast(str(path), max_pixels=max_pixels))
    except StudioError:
        raise
    except Exception:
        _fail("sprite inspection failed")
    label = _safe_name(path)
    craft["file"] = label
    return {"file": label, "critique": craft, "contrast": contrast}


def load_preview(
        image: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str],
        max_pixels: int = MAX_IMAGE_PIXELS) -> Image.Image:
    """Load one trusted PNG into memory for nearest-neighbor presentation."""
    max_pixels = _pixel_limit(max_pixels)
    root = _workspace(trusted_root)
    _path, preview = _validated_png(image, root, max_pixels=max_pixels)
    return preview


def load_user_palette(
        palette_file: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str]) -> dict:
    """Load one bounded, user-owned palette from the trusted workspace."""
    root = _workspace(trusted_root)
    path = _resolve_input(palette_file, root, suffixes=(".json",))
    try:
        colors, metadata = load_palette_file(path, trusted_root=root)
    except StudioError:
        raise
    except Exception:
        _fail("user palette is invalid")
    return {
        "file": _safe_name(path),
        "colors": list(colors),
        "metadata": dict(metadata),
    }


def _quantize_palette(
        palette: str | None,
        palette_file: str | os.PathLike[str] | None,
        root: Path) -> tuple[str, dict]:
    if (palette is None) == (palette_file is None):
        _fail("choose one palette source")
    if palette_file is not None:
        path = _resolve_input(palette_file, root, suffixes=(".json",))
        return _safe_name(path), {
            "palette_file": str(path),
            "palette_root": str(root),
        }
    if not isinstance(palette, str) or not palette:
        _fail("palette is unavailable")
    return palette, {}


def preview_quantization(
        image: str | os.PathLike[str],
        palette: str | None,
        *,
        trusted_root: str | os.PathLike[str],
        palette_file: str | os.PathLike[str] | None = None,
        max_pixels: int = MAX_IMAGE_PIXELS) -> dict:
    """Quantize one trusted PNG into a disposable, in-memory preview."""
    max_pixels = _pixel_limit(max_pixels)
    root = _workspace(trusted_root)
    source, _image = _validated_png(image, root, max_pixels=max_pixels)
    palette_label, palette_options = _quantize_palette(palette, palette_file, root)
    try:
        with tempfile.TemporaryDirectory(prefix="lenkraster-studio-preview-") as raw_temp:
            output = Path(raw_temp) / "preview.png"
            _path, colors_used, used = quantize_file(
                str(source),
                palette,
                str(output),
                max_pixels=max_pixels,
                **palette_options,
            )
            preview = _read_png(output, max_pixels=max_pixels)
    except StudioError:
        raise
    except Exception:
        _fail("palette preview failed")
    return {
        "image": preview,
        "palette": palette_label,
        "colors_used": colors_used,
        "used_colors": list(used),
    }


def export_quantized_png(
        image: str | os.PathLike[str],
        palette: str | None,
        output: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str],
        palette_file: str | os.PathLike[str] | None = None,
        max_pixels: int = MAX_IMAGE_PIXELS) -> dict:
    """Create a new palette-quantized PNG without replacing any existing file."""
    max_pixels = _pixel_limit(max_pixels)
    root = _workspace(trusted_root)
    source, _image = _validated_png(image, root, max_pixels=max_pixels)
    destination = _resolve_output_png(output, root)
    _palette_label, palette_options = _quantize_palette(palette, palette_file, root)
    temporary = None
    try:
        _revalidate_output_parent(destination, root)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".lenkraster-studio-",
            suffix=".png",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        _path, colors_used, used = quantize_file(
            str(source),
            palette,
            str(temporary),
            max_pixels=max_pixels,
            **palette_options,
        )
        _validate_staged_png(temporary, max_pixels=max_pixels)
        _revalidate_output_parent(destination, root)
        _publish_create_only(temporary, destination)
    except StudioError:
        raise
    except Exception:
        _fail("quantized export failed")
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return {
        "file": _safe_name(destination),
        "colors_used": colors_used,
        "used_colors": list(used),
    }


def _dither_preview_image(
        color_a: str,
        color_b: str,
        *,
        size: int,
        order: int,
        max_pixels: int) -> tuple[Image.Image, list[str]]:
    max_pixels = _pixel_limit(max_pixels)
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        _fail("dither is invalid")
    if isinstance(order, bool) or not isinstance(order, int) or order not in (2, 4):
        _fail("dither is invalid")
    try:
        colors = [rgb2hex(hex2rgb(color_a)), rgb2hex(hex2rgb(color_b))]
        pixels = dither_image(
            colors[0],
            colors[1],
            size=size,
            order=order,
            max_pixels=max_pixels,
        )
        return Image.fromarray(pixels), colors
    except Exception:
        _fail("dither is invalid")


def preview_dither(
        color_a: str,
        color_b: str,
        *,
        size: int = 48,
        order: int = 4,
        max_pixels: int = MAX_IMAGE_PIXELS) -> dict:
    """Return one bounded ordered-dither test pattern without writing a file."""
    image, colors = _dither_preview_image(
        color_a,
        color_b,
        size=size,
        order=order,
        max_pixels=max_pixels,
    )
    return {
        "image": image,
        "colors": colors,
        "order": order,
        "size": list(image.size),
    }


def export_dither_png(
        color_a: str,
        color_b: str,
        output: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str],
        size: int = 48,
        order: int = 4,
        max_pixels: int = MAX_IMAGE_PIXELS) -> dict:
    """Create a bounded ordered-dither PNG without replacing an existing file."""
    root = _workspace(trusted_root)
    destination = _resolve_output_png(output, root)
    image, colors = _dither_preview_image(
        color_a,
        color_b,
        size=size,
        order=order,
        max_pixels=max_pixels,
    )
    temporary = None
    try:
        _revalidate_output_parent(destination, root)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".lenkraster-studio-",
            suffix=".png",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        image.save(temporary, format="PNG")
        _validate_staged_png(temporary, max_pixels=max_pixels)
        _revalidate_output_parent(destination, root)
        _publish_create_only(temporary, destination)
    except StudioError:
        raise
    except Exception:
        _fail("dither export failed")
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return {
        "file": _safe_name(destination),
        "colors": colors,
        "order": order,
        "size": list(image.size),
    }


def build_ramp(base_hex: str, *, stops: int = 5, drift: float = -8.0) -> list[str]:
    """Build an OKLCH material ramp for Studio presentation."""
    try:
        return list(make_ramp(base_hex, stops=stops, drift=drift))
    except Exception:
        _fail("color ramp is invalid")


def _ramp_image(colors: Sequence[str], swatch_size: int, max_pixels: int) -> Image.Image:
    max_pixels = _pixel_limit(max_pixels)
    if (
        isinstance(swatch_size, bool)
        or not isinstance(swatch_size, int)
        or swatch_size < 1
    ):
        _fail("swatch size is invalid")
    width = swatch_size * len(colors)
    height = swatch_size
    if width * height > max_pixels:
        _fail("image exceeds safety limits")
    canvas = Image.new("RGB", (width, height))
    try:
        for index, color in enumerate(colors):
            rgb = tuple(int(channel) for channel in hex2rgb(color))
            tile = Image.new("RGB", (swatch_size, swatch_size), rgb)
            canvas.paste(tile, (index * swatch_size, 0))
    except Exception:
        _fail("color ramp is invalid")
    return canvas


def preview_ramp(
        base_hex: str,
        *,
        stops: int = 5,
        drift: float = -8.0,
        swatch_size: int = 48,
        max_pixels: int = MAX_IMAGE_PIXELS) -> dict:
    """Return one ramp and its in-memory swatch strip."""
    colors = build_ramp(base_hex, stops=stops, drift=drift)
    image = _ramp_image(colors, swatch_size, max_pixels)
    return {"colors": colors, "image": image, "size": list(image.size)}


def export_ramp_png(
        base_hex: str,
        output: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str],
        stops: int = 5,
        drift: float = -8.0,
        swatch_size: int = 48,
        max_pixels: int = MAX_IMAGE_PIXELS) -> dict:
    """Create a new PNG strip for one generated material ramp."""
    max_pixels = _pixel_limit(max_pixels)
    root = _workspace(trusted_root)
    destination = _resolve_output_png(output, root)
    colors = build_ramp(base_hex, stops=stops, drift=drift)
    image = _ramp_image(colors, swatch_size, max_pixels)
    temporary = None
    try:
        _revalidate_output_parent(destination, root)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".lenkraster-studio-",
            suffix=".png",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        image.save(temporary, format="PNG")
        _validate_staged_png(temporary, max_pixels=max_pixels)
        _revalidate_output_parent(destination, root)
        _publish_create_only(temporary, destination)
    except StudioError:
        raise
    except Exception:
        _fail("ramp export failed")
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return {
        "file": _safe_name(destination),
        "colors": colors,
        "size": list(image.size),
    }


def analyze_cycle(
        frames: Sequence[str | os.PathLike[str]],
        *,
        trusted_root: str | os.PathLike[str],
        motion_threshold: float = 15.0,
        min_motion_pixels: int = 4) -> dict:
    """Run bounded animation QA on an explicit, ordered list of trusted PNGs."""
    if isinstance(frames, (str, bytes, os.PathLike)):
        _fail("choose animation frames explicitly")
    try:
        selected = list(frames)
    except TypeError:
        _fail("choose animation frames explicitly")
    if len(selected) < 2:
        _fail("choose at least two animation frames")
    if len(selected) > MAX_FRAMES:
        _fail("animation exceeds safety limits")
    root = _workspace(trusted_root)
    paths = []
    labels = []
    for item in selected:
        path, _image = _validated_png(item, root, max_pixels=MAX_IMAGE_PIXELS)
        paths.append(path)
        labels.append(_safe_name(path))
    try:
        report = dict(qa_cycle(
            [str(path) for path in paths],
            motion_threshold=motion_threshold,
            min_motion_pixels=min_motion_pixels,
            max_frames=MAX_FRAMES,
            max_frame_pixels=MAX_IMAGE_PIXELS,
            max_total_pixels=MAX_TOTAL_PIXELS,
            max_pair_pixels=MAX_PAIR_PIXELS,
        ))
        previous_labels = list(report.get("frame_names", []))
        report["frame_names"] = labels
        replacements = {
            str(path): label for path, label in zip(paths, labels)
        }
        replacements.update({
            str(previous): label
            for previous, label in zip(previous_labels, labels)
        })
        for key in ("issues", "hints"):
            cleaned = []
            for raw in report.get(key, []):
                text = str(raw)
                for previous, label in replacements.items():
                    text = text.replace(previous, label)
                cleaned.append(text)
            report[key] = cleaned
        report = _sanitize_report(report, root, replacements)
    except StudioError:
        raise
    except Exception:
        _fail("animation analysis failed")
    return report


def _sanitize_report(value, root: Path, replacements: dict[str, str]):
    if isinstance(value, dict):
        return {
            _sanitize_report(key, root, replacements):
            _sanitize_report(child, root, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_report(child, root, replacements) for child in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_report(child, root, replacements) for child in value)
    if isinstance(value, str):
        cleaned = value
        for private, label in replacements.items():
            cleaned = cleaned.replace(private, label)
            cleaned = cleaned.replace(private.replace("\\", "/"), label)
        for root_text in (str(root), root.as_posix()):
            cleaned = cleaned.replace(root_text, "<workspace>")
        return cleaned
    return value


def export_aseprite(
        document: str | os.PathLike[str],
        output_directory: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str],
        executable: str | os.PathLike[str] | None = None,
        executable_sha256: str | None = None,
        tag: str | None = None,
        layer: str | None = None) -> dict:
    """Export a trusted Aseprite document through the hardened native bridge."""
    root = _workspace(trusted_root)
    source = _resolve_input(document, root, suffixes=(".ase", ".aseprite"))
    _validate_aseprite_output(output_directory, root)
    try:
        return export_aseprite_document(
            str(source),
            os.fspath(output_directory),
            trusted_root=root,
            executable=executable,
            executable_sha256=executable_sha256,
            tag=tag,
            layer=layer,
        )
    except AsepriteError as error:
        message = str(error)
        if message in _SAFE_ASEPRITE_ERRORS:
            raise StudioError(message) from None
        _fail("Aseprite export failed")
    except Exception:
        _fail("Aseprite export failed")


def qa_aseprite(
        document: str | os.PathLike[str],
        *,
        trusted_root: str | os.PathLike[str],
        executable: str | os.PathLike[str] | None = None,
        executable_sha256: str | None = None,
        tag: str | None = None,
        layer: str | None = None,
        motion_threshold: float = 15.0,
        min_motion_pixels: int = 4) -> dict:
    """Run bounded cycle QA through the hardened Aseprite bridge."""
    root = _workspace(trusted_root)
    source = _resolve_input(document, root, suffixes=(".ase", ".aseprite"))
    try:
        return qa_aseprite_document(
            str(source),
            trusted_root=root,
            executable=executable,
            executable_sha256=executable_sha256,
            tag=tag,
            layer=layer,
            motion_threshold=motion_threshold,
            min_motion_pixels=min_motion_pixels,
        )
    except AsepriteError as error:
        message = str(error)
        if message in _SAFE_ASEPRITE_ERRORS:
            raise StudioError(message) from None
        _fail("Aseprite analysis failed")
    except Exception:
        _fail("Aseprite analysis failed")


__all__ = [
    "MAX_FRAMES",
    "MAX_IMAGE_PIXELS",
    "MAX_PAIR_PIXELS",
    "MAX_TOTAL_PIXELS",
    "StudioError",
    "analyze_cycle",
    "available_palettes",
    "build_ramp",
    "export_aseprite",
    "export_dither_png",
    "export_quantized_png",
    "export_ramp_png",
    "inspect_sprite",
    "load_palette",
    "load_preview",
    "load_user_palette",
    "preview_dither",
    "preview_quantization",
    "preview_ramp",
    "qa_aseprite",
]
