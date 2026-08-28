"""lenkraster.palette -- OKLCH color ramps, bounded palettes, quantization, dither.

Color math follows Bjorn Ottosson's published OKLab specification. Material ramps use
perceptual lightness spacing, tapered chroma, and caller-controlled hue drift.

LenkRaster ships only the small palette data files explicitly listed as built-ins. Users
may supply their own strictly validated palette JSON below an explicit trusted root.
"""
import json
import math
import os
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

__all__ = ["make_ramp", "load_palette", "load_palette_file", "available_palettes", "quantize_file",
           "check_contrast", "dither_image", "hex2rgb", "rgb2hex",
           "rgb_to_oklab", "oklab_to_rgb"]

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "palettes")
_BUILTIN_PALETTES = (
    "lenk-cinder-16",
    "lenk-fern-4",
    "lenk-signal-16",
    "lenk-studio-32",
)
_MAX_PALETTE_BYTES = 16 * 1024
_MAX_PALETTE_PATH_CHARS = 1024
_MAX_PALETTE_COLORS = 64
_MAX_METADATA_CHARS = 128
_MAX_METADATA_BYTES = 256
_MAX_ENCODED_IMAGE_BYTES = 16 * 1024 * 1024
_PALETTE_KEYS = frozenset(("name", "author", "colors"))
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

# ---------------- OKLab (Ottosson constants) ----------------

def _srgb_to_linear(x):
    x = x / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(x):
    x = np.clip(x, 0, 1)
    v = np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)
    return np.round(v * 255)


def rgb_to_oklab(rgb):
    """RGB [0..255] float array (..., 3) -> OKLab (..., 3)."""
    r = _srgb_to_linear(rgb[..., 0])
    g = _srgb_to_linear(rgb[..., 1])
    b = _srgb_to_linear(rgb[..., 2])
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return np.stack([L, A, B], axis=-1)


def oklab_to_rgb(lab):
    """OKLab array -> RGB [0..255] rounded."""
    L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
    l_ = L + 0.3963377774 * A + 0.2158037573 * B
    m_ = L - 0.1055613458 * A - 0.0638541728 * B
    s_ = L - 0.0894841775 * A - 1.2914855480 * B
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return np.stack([_linear_to_srgb(r), _linear_to_srgb(g), _linear_to_srgb(b)], axis=-1)


def hex2rgb(h):
    """'#rrggbb' (leading # optional) -> float RGB array. Raises ValueError clearly."""
    h = (h or "").strip().lstrip("#")
    if len(h) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in h):
        raise ValueError(f"color must be a hex string like '#d77643', got {h!r}")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def rgb2hex(c):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c)


def _oklch_of(rgb):
    L, A, B = rgb_to_oklab(rgb.reshape(1, 1, 3))[0, 0]
    return float(L), math.hypot(A, B), math.degrees(math.atan2(B, A))


def _oklch_to_rgb(L, C, Hdeg):
    rad = math.radians(Hdeg)
    lab = np.array([[[L, C * math.cos(rad), C * math.sin(rad)]]])
    return oklab_to_rgb(lab)[0, 0]


def _in_gamut(rgb):
    return bool(np.all(rgb > -0.5) and np.all(rgb < 255.5))


def _clamp_chroma(L, C, Hdeg):
    """Binary-search chroma down until the color fits sRGB (never clip channels)."""
    lo, hi = 0.0, C
    best = _oklch_to_rgb(L, 0.0, Hdeg)
    for _ in range(24):
        mid = (lo + hi) / 2
        rgb = _oklch_to_rgb(L, mid, Hdeg)
        if _in_gamut(rgb):
            lo = mid
            best = rgb
        else:
            hi = mid
    return best


# ---------------- ramp generation ----------------

def make_ramp(base_hex, stops=5, drift=-8.0, L_min=0.16, L_max=0.97):
    """Hue-shifted material ramp from a base color, in OKLCH.

    Args:
        base_hex: '#rrggbb' anchor color (placed at its natural relative position).
        stops: number of swatches (3-9 typical at sprite scale).
        drift: hue degrees per step toward DARK. Negative values walk shadows toward
            blue/purple; positive values walk them toward yellow/green.
        L_min/L_max: lightness floor/ceiling.

    Returns list of '#rrggbb' strings, dark to light.
    """
    if stops < 1:
        raise ValueError("ramp must contain at least 1 stop")
    if stops > 64:
        raise ValueError("ramp may contain at most 64 stops")
    if stops < 2:
        return [rgb2hex(hex2rgb(base_hex))]
    L0, C0, H0 = _oklch_of(hex2rgb(base_hex))
    t0 = min(max((L0 - L_min) / (L_max - L_min), 0.05), 0.95)
    out = []
    for i in range(stops):
        t = i / (stops - 1)
        te = t ** 1.08  # eased spacing: perceptually even steps
        L = L_min + (L_max - L_min) * te
        taper = 1.0 - 0.55 * max(0.0, (abs(L - 0.55) - 0.15) / 0.45)
        C = C0 * max(0.25, taper)
        steps_from_base = (t - t0) / (1.0 / (stops - 1))
        H = H0 + drift * steps_from_base
        out.append(rgb2hex(_clamp_chroma(L, C, H)))
    return out


# ---------------- palette library ----------------

def _read_bounded(path, maximum, label):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(maximum + 1)
    except (OSError, TypeError, ValueError):
        raise ValueError(f"{label} is unavailable") from None
    if len(raw) > maximum:
        raise ValueError(f"{label} exceeds {maximum} byte limit")
    return raw


def _reject_json_value(_value):
    raise ValueError("unsupported JSON value")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _metadata_is_valid(value, allow_empty):
    if type(value) is not str:
        return False
    if (not allow_empty and not value) or len(value) > _MAX_METADATA_CHARS:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(encoded) > _MAX_METADATA_BYTES or value != value.strip():
        return False
    return all(ord(character) >= 32 and ord(character) != 127 for character in value)


def _decode_palette(raw, label="built-in palette"):
    invalid = f"{label} data is invalid"
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_int=_reject_json_value,
            parse_float=_reject_json_value,
            parse_constant=_reject_json_value,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise ValueError(invalid) from None
    if type(data) is not dict or frozenset(data) != _PALETTE_KEYS:
        raise ValueError(invalid)
    if not _metadata_is_valid(data["name"], allow_empty=False):
        raise ValueError(invalid)
    if not _metadata_is_valid(data["author"], allow_empty=True):
        raise ValueError(invalid)
    colors = data["colors"]
    if type(colors) is not list or not 2 <= len(colors) <= _MAX_PALETTE_COLORS:
        raise ValueError(invalid)
    if any(
        type(color) is not str
        or len(color) != 6
        or any(character not in _HEX_DIGITS for character in color)
        for color in colors
    ):
        raise ValueError(invalid)
    if len({color.lower() for color in colors}) != len(colors):
        raise ValueError(invalid)
    return data


def _palette_result(data):
    metadata = {"name": data["name"], "author": data["author"]}
    colors = ["#" + color for color in data["colors"]]
    return colors, metadata


def available_palettes():
    """Stable names of retained built-in palettes."""
    return list(_BUILTIN_PALETTES)


def load_palette(name):
    """Load a built-in palette by name -> list of '#hex' (stored without '#')."""
    if type(name) is not str or name not in _BUILTIN_PALETTES:
        raise KeyError(f"palette not found; available: {', '.join(_BUILTIN_PALETTES)}")
    root = Path(_DATA_DIR)
    path = root / f"{name}.json"
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError:
        raise ValueError("built-in palette data is unavailable") from None
    if resolved_path.parent != resolved_root or not resolved_path.is_file():
        raise ValueError("built-in palette data is unavailable")
    data = _decode_palette(_read_bounded(
        resolved_path,
        _MAX_PALETTE_BYTES,
        "built-in palette",
    ))
    return _palette_result(data)


def load_palette_file(path, *, trusted_root):
    """Load one user-owned palette JSON contained by an explicit trusted root."""
    try:
        root = Path(trusted_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError("palette trusted root is unavailable") from None
    if not root.is_dir():
        raise ValueError("palette trusted root is unavailable")
    try:
        raw_path = os.fspath(path)
    except TypeError:
        raise ValueError("user palette is unavailable") from None
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or len(raw_path) > _MAX_PALETTE_PATH_CHARS
        or "\x00" in raw_path
    ):
        raise ValueError("user palette is unavailable")
    requested = Path(raw_path)
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("user palette is unavailable") from None
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError("user palette is outside trusted root") from None
    if not resolved.is_file() or resolved.suffix.lower() != ".json":
        raise ValueError("user palette is unavailable")
    data = _decode_palette(
        _read_bounded(resolved, _MAX_PALETTE_BYTES, "user palette"),
        "user palette",
    )
    return _palette_result(data)


def quantize_file(path, palette_name, out_path=None, keep_alpha=True,
                  max_pixels=1_048_576, *, palette_file=None, palette_root=None):
    """Snap an image to one built-in or user-owned palette in OKLab space.

    SHARED-PALETTE LAW: for animation frames, snap all frames against one target palette
    (or cluster frames together first); never let each frame find its own colors or the
    cycle will flicker between slightly-different materials.

    Returns (out_path, n_colors_used, used_hex_list).
    """
    if (palette_name is None) == (palette_file is None):
        raise ValueError("choose one palette source")
    if palette_file is not None:
        pal, _metadata = load_palette_file(
            palette_file,
            trusted_root=palette_root,
        )
        output_label = "user-palette"
    else:
        pal, _metadata = load_palette(palette_name)
        output_label = palette_name
    raw = _read_bounded(path, _MAX_ENCODED_IMAGE_BYTES, "image")
    with Image.open(BytesIO(raw)) as img:
        if img.width * img.height > max_pixels:
            raise ValueError(f"image exceeds {max_pixels} pixel limit")
        has_alpha = keep_alpha and (img.mode in ("RGBA", "LA") or "transparency" in img.info)
        rgba = np.array(img.convert("RGBA"), dtype=np.float64)
    alpha = rgba[..., 3:4]
    rgb = rgba[..., :3]
    h, w = rgb.shape[:2]
    flat = rgb.reshape(-1, 3)
    palette_rgb = np.stack([hex2rgb(hp) for hp in pal])
    plab = rgb_to_oklab(palette_rgb)
    visible = alpha.reshape(-1) > 0 if has_alpha else np.ones(h * w, dtype=bool)
    snapped_flat = np.zeros((h * w, 3), dtype=np.uint8)
    visible_rgb = flat[visible]
    idx = np.empty(len(visible_rgb), dtype=np.int64)
    if len(visible_rgb):
        labs = rgb_to_oklab(visible_rgb.reshape(-1, 1, 3))[:, 0, :]
        for i in range(0, len(labs), 4096):
            d = ((labs[i:i + 4096, None, :] - plab[None, :, :]) ** 2).sum(-1)
            idx[i:i + 4096] = d.argmin(1)
        snapped_flat[visible] = palette_rgb[idx].astype(np.uint8)
    snapped = snapped_flat.reshape(h, w, 3)
    if out_path is None:
        base = os.path.splitext(str(path))[0]
        out_path = base + f".{output_label}.png"
    if has_alpha:
        out_img = np.concatenate([snapped, alpha.astype(np.uint8)], axis=-1)
        Image.fromarray(out_img, "RGBA").save(out_path)
    else:
        Image.fromarray(snapped, "RGB").save(out_path)
    used = sorted({pal[j] for j in idx.tolist()})
    return out_path, len(used), used


# ---------------- contrast check ----------------

def check_contrast(path_or_image, min_dl=0.055, bucket=30,
                   max_pixels=1_048_576):
    """Same-hue adjacent lightness gate: flags pairs of shades that melt together at 1x.
    Cross-hue pairs at equal lightness are fine and never flagged.

    Returns dict with per-bucket weak pairs and a summary string.
    """
    if isinstance(path_or_image, (str, bytes, os.PathLike)):
        raw = _read_bounded(path_or_image, _MAX_ENCODED_IMAGE_BYTES, "image")
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > max_pixels:
                raise ValueError(f"image exceeds {max_pixels} pixel limit")
            rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    else:
        source = np.asarray(path_or_image)
        if source.ndim != 3 or source.shape[-1] not in (3, 4):
            raise ValueError("image array must have shape (height, width, 3 or 4)")
        if source.shape[0] * source.shape[1] > max_pixels:
            raise ValueError(f"image exceeds {max_pixels} pixel limit")
        rgba = source.astype(np.uint8, copy=False)
    visible = rgba[..., 3] > 128 if rgba.shape[-1] >= 4 else np.ones(rgba.shape[:2], dtype=bool)
    flat = rgba[..., :3][visible].reshape(-1, 3)
    if not len(flat):
        return {"summary": "fewer than 2 significant colors", "weak": []}
    uniq, counts = np.unique(flat.astype(np.uint8), axis=0, return_counts=True)
    cols = uniq[counts >= 8].astype(np.float64)
    if len(cols) < 2:
        return {"summary": "fewer than 2 significant colors", "weak": []}
    labs = rgb_to_oklab(cols.reshape(-1, 1, 3))[:, 0, :]
    hues = np.degrees(np.arctan2(labs[:, 2], labs[:, 1])) % 360
    weak = []
    total_pairs = 0
    for b in range(0, 360, bucket):
        sel = np.abs(((hues - b + 180) % 360) - 90) < 15
        if sel.sum() < 2:
            continue
        order = np.argsort(labs[sel][:, 0])
        fam = labs[sel][order][:, 0]
        gaps = np.diff(fam)
        total_pairs += len(gaps)
        weak.extend(float(fam[i]) for i in range(len(gaps)) if gaps[i] < min_dl)
    summary = (f"{len(weak)} of {total_pairs} same-hue pairs below dL={min_dl}"
               if weak else
               f"all {total_pairs} same-hue steps >= dL={min_dl}: good separation")
    return {"summary": summary, "weak": sorted(weak)[:12],
            "n_colors": int(len(cols)),
            "L_range": [float(labs[:, 0].min()), float(labs[:, 0].max())]}


# ---------------- dither ----------------

BAYER4 = np.array([[0, 8, 2, 10],
                   [12, 4, 14, 6],
                   [3, 11, 1, 9],
                   [15, 7, 13, 5]], dtype=np.float64) / 16.0
BAYER2 = np.array([[0, 2], [3, 1]], dtype=np.float64) / 4.0


def dither_image(hex_a, hex_b, size=48, order=4, max_pixels=1_048_576):
    """Ordered Bayer blend of two colors along a diagonal gradient (test pattern)."""
    if size < 1:
        raise ValueError("dither size must be positive")
    if size * size > max_pixels:
        raise ValueError(f"dither exceeds {max_pixels} pixel limit")
    if order not in (2, 4):
        raise ValueError("dither order must be 2 or 4")
    a, b = hex2rgb(hex_a), hex2rgb(hex_b)
    thr = BAYER4 if order == 4 else BAYER2
    yy, xx = np.mgrid[0:size, 0:size]
    t = ((xx + yy) % 8) / 8.0
    mask = thr[yy % thr.shape[0], xx % thr.shape[1]] < t
    return np.where(mask[..., None], b[None, None, :], a[None, None, :]).astype(np.uint8)
