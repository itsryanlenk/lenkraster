"""lenkraster.critic -- programmatic craft critic for pixel-art sprites.

Encodes community craft rules (PixelJoint's consolidated tutorial, Slynyrd pixelblogs,
the Pixelation tradition) as measurable checks on an RGBA image. Every finding carries
a severity, optional pixel locations, and a plain-language fix hint suitable for feeding
back to a generation agent as a retry hint.

Rules and thresholds are documented with citations in skills/references/craft-rules.md.
"""
import math
from collections import Counter, deque
from io import BytesIO

import numpy as np
from PIL import Image

__all__ = ["critique", "load", "CHECK_NAMES", "FINDING_NAMES"]

_MAX_ENCODED_IMAGE_BYTES = 16 * 1024 * 1024
# Aggregate full-frame mask pixels allowed across significant banding colors.
_MAX_BANDING_MASK_PIXELS = 32 * 1024 * 1024

# ---------- helpers ----------


def _read_encoded_image(path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(_MAX_ENCODED_IMAGE_BYTES + 1)
    except (OSError, TypeError):
        raise ValueError("sprite image could not be read") from None
    if len(raw) > _MAX_ENCODED_IMAGE_BYTES:
        raise ValueError(
            f"sprite exceeds {_MAX_ENCODED_IMAGE_BYTES} encoded byte limit")
    return raw


def load(path, max_pixels=1_048_576):
    """Load an image as an int32 RGBA numpy array."""
    raw = _read_encoded_image(path)
    try:
        image = Image.open(BytesIO(raw))
    except (Image.DecompressionBombError, OSError, ValueError):
        raise ValueError("sprite is not a valid image") from None
    try:
        if image.width * image.height > max_pixels:
            raise ValueError(f"sprite exceeds {max_pixels} pixel limit")
        try:
            return np.array(image.convert("RGBA"), dtype=np.int32)
        except (OSError, ValueError):
            raise ValueError("sprite is not a valid image") from None
    finally:
        image.close()


def _srgb_to_l(arr):
    """Relative luminance (WCAG 2.x) of RGB array [0..255] -> [0..1]."""
    a = arr / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def _components(mask):
    """4-connected components of a boolean mask -> list of coordinate lists."""
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    h, w = mask.shape
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not seen[y, x]:
                q = deque([(y, x)])
                seen[y, x] = True
                coords = []
                while q:
                    cy, cx = q.popleft()
                    coords.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                comps.append(coords)
    return comps


def _edge_and_inner(px):
    """Split opaque pixels into outline (touching transparency) and inner."""
    a = px[..., 3] > 128
    h, w = a.shape
    edge, inner = [], []
    for y in range(h):
        for x in range(w):
            if not a[y, x]:
                continue
            is_edge = False
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if ny < 0 or nx < 0 or ny >= h or nx >= w or not a[ny, nx]:
                    is_edge = True
                    break
            (edge if is_edge else inner).append((y, x))
    return edge, inner


def _near_duplicate_pairs(colors, max_l1, limit=256, max_comparisons=65_536):
    """Count nearby RGB pairs with bounded work using spatial buckets.

    The finding's severity is independent of the exact pair count, so stop once
    enough evidence is collected instead of comparing every palette pair. The
    explicit channel-comparison budget fails closed on adversarial color sets.
    """
    if max_l1 <= 0 or len(colors) < 2:
        return 0, False
    if isinstance(max_comparisons, bool) or not isinstance(max_comparisons, int) \
            or max_comparisons < 1:
        raise ValueError("max_comparisons must be a positive integer")
    buckets = {}
    pairs = 0
    comparisons = 0
    for color in colors:
        rgb = tuple(int(channel) for channel in color)
        cell = tuple(channel // max_l1 for channel in rgb)
        for dr in (-1, 0, 1):
            for dg in (-1, 0, 1):
                for db in (-1, 0, 1):
                    nearby = buckets.get((cell[0] + dr, cell[1] + dg, cell[2] + db), ())
                    for other in nearby:
                        distance = 0
                        for left, right in zip(rgb, other):
                            if comparisons >= max_comparisons:
                                raise RuntimeError(
                                    "near-duplicate color scan exceeded work limit")
                            comparisons += 1
                            distance += abs(left - right)
                        if distance < max_l1:
                            pairs += 1
                            if pairs >= limit:
                                return pairs, True
        buckets.setdefault(cell, []).append(rgb)
    return pairs, False


# ---------- checks ----------

def check_orphans(px, min_cluster=2):
    """Single opaque pixels surrounded by unrelated colors: stray AA/JPEG noise.
    Real details come in clusters of 2+ (cure: 'pixels travel in packs')."""
    a = px[..., 3] > 128
    if not a.any():
        return []
    comps = _components(a)
    tiny = [c for c in comps if len(c) < min_cluster]
    out = []
    if tiny and len(tiny) / max(len(comps), 1) > 0.15:
        locs = [[int(c[0][1]), int(c[0][0])] for c in tiny[:8]]
        out.append(dict(
            check="orphan_pixels", severity=0.6,
            detail=f"{len(tiny)} of {len(comps)} regions are 1px specks",
            locs=locs,
            hint="Remove stray single pixels; group detail into clusters of 2+ px."))
    return out


def check_jaggies(px):
    """Uneven stair rhythm on long diagonal edges. Pixel-perfect diagonals keep
    run lengths that change by steps of 1, never random jumps."""
    a = px[..., 3] > 128
    h, w = a.shape
    rhythms = []
    for target in (a, ~a):
        visited = np.zeros_like(target, dtype=bool)
        for y in range(1, h):
            for x in range(1, w):
                if target[y, x] and not visited[y, x]:
                    steps = []
                    cy, cx = y, x
                    while 0 <= cy < h and 0 <= cx < w and target[cy, cx] and not visited[cy, cx]:
                        visited[cy, cx] = True
                        run = 0
                        while (cx + 1 < w and target[cy, cx + 1] and not visited[cy, cx + 1]):
                            cx += 1
                            run += 1
                            visited[cy, cx] = True
                        steps.append(max(run, 1))
                        cy -= 1
                        cx += 1
                    if sum(steps) >= 12:
                        rhythms.append(steps)
    bad = [r for r in rhythms if len(r) >= 4 and (max(r) - min(r)) >= 3 and Counter(r)[1] >= 3]
    out = []
    if bad:
        out.append(dict(
            check="jagged_stairs", severity=0.4,
            detail=f"{len(bad)} diagonal edges have uneven stair rhythm",
            locs=[],
            hint="Smooth long diagonals with run lengths that change by 1 "
                 "(e.g. 2,2,1 then 3,3,2); never random mixes."))
    return out


def check_banding(px):
    """Banding = 2+ parallel AA lines stacked along one edge, the line color being
    an intermediate shade between two others. One AA line per edge is good craft;
    stacked ridges expose the grid (cure's 'AA banding' / 'hugging')."""
    a = px[..., 3] > 128
    h, w = a.shape
    rgb = px[..., :3]
    hits = 0
    locs = []
    worst = None
    colors, counts = np.unique(rgb[a], axis=0, return_counts=True)
    frame_pixels = h * w
    mask_pixels = 0
    for c, cnt in zip(colors, counts):
        if cnt < 8:
            continue
        if mask_pixels > _MAX_BANDING_MASK_PIXELS - frame_pixels:
            raise RuntimeError("banding scan exceeded work limit")
        mask_pixels += frame_pixels
        m = a & (rgb[..., 0] == c[0]) & (rgb[..., 1] == c[1]) & (rgb[..., 2] == c[2])
        diag_len = {}
        ys, xs = np.where(m)
        for y, x in zip(ys, xs):
            d = int(x + y)
            diag_len[d] = diag_len.get(d, 0) + 1
        ds = sorted(d for d, n in diag_len.items() if n >= 3)
        if not ds:
            continue
        streak = [ds[0]]
        best = []
        for i in range(1, len(ds)):
            if ds[i] - ds[i - 1] == 1:
                streak.append(ds[i])
            else:
                if len(streak) > len(best):
                    best = streak
                streak = [ds[i]]
        if len(streak) > len(best):
            best = streak
        if len(best) < 2:
            continue
        # intermediate-shade test across perpendicular neighbors
        best_diagonals = set(best)
        ok = tot = 0
        for y, x in zip(ys, xs):
            if int(x + y) in best_diagonals:
                tot += 1
                na = (y - 1, x - 1)
                nb = (y + 1, x + 1)
                ca = tuple(rgb[na]) if (0 <= na[0] < h and 0 <= na[1] < w and a[na]) else None
                cb = tuple(rgb[nb]) if (0 <= nb[0] < h and 0 <= nb[1] < w and a[nb]) else None
                if ca and cb and ca != cb and ca != tuple(c) and cb != tuple(c):
                    ok += 1
        if tot and ok / tot >= 0.6:
            hits += 1
            mid = len(ys) // 2
            if worst is None or len(best) > worst[0]:
                worst = (len(best), [int(xs[mid]), int(ys[mid])])
            if len(locs) < 6:
                locs.append([int(xs[0]), int(ys[0])])
    out = []
    if hits >= 1:
        out.append(dict(
            check="banding", severity=min(0.85, 0.35 + 0.12 * hits),
            detail=f"{hits} colors form stacked parallel AA ridges"
                   + (f" (longest {worst[0]} lines)" if worst else ""),
            locs=locs,
            hint="Banding: AA stacked in parallel ridges. Keep at most ONE aa line per "
                 "edge, vary its length, place aa inside corners not against backgrounds."))
    return out


def check_pillow(px, r_threshold=0.55):
    """Pillow shading: luminance tracks distance-from-outline uniformly, meaning
    shading follows the flat shape rather than any light direction."""
    a = px[..., 3] > 128
    lum = _srgb_to_l(px[..., :3])
    h, w = a.shape
    dist = np.full((h, w), 1e9)
    q = deque()
    for y in range(h):
        for x in range(w):
            if not a[y, x] or y in (0, h - 1) or x in (0, w - 1):
                dist[y, x] = 0
                q.append((y, x))
    while q:
        cy, cx = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx < w and dist[ny, nx] > dist[cy, cx] + 1:
                dist[ny, nx] = dist[cy, cx] + 1
                q.append((ny, nx))
    ys, xs = np.where(a & (dist > 0) & (dist < 1e9))
    if len(ys) < 60:
        return []
    d_vals = dist[ys, xs]
    l_vals = lum[ys, xs]
    # zero-variance guards: corrcoef would divide by zero and emit NaN warnings
    if float(d_vals.std()) == 0.0 or float(l_vals.std()) == 0.0:
        return []
    corr = float(np.corrcoef(d_vals, l_vals)[0, 1])
    out = []
    if abs(corr) > r_threshold:
        out.append(dict(
            check="pillow_shading", severity=0.75,
            detail=f"luminance tracks distance-from-edge at r={corr:.2f} (uniform halo)",
            locs=[],
            hint="Pillow shading suspected: light/dark follows the outline evenly. Pick ONE "
                 "light direction; shade forms away from it; keep lit faces flat."))
    return out


def check_outline(px):
    """Pure-black outlines flatten palettes; outline dominating opaque area makes
    sprites read as drawings instead of shapes."""
    a = px[..., 3] > 128
    edge, _ = _edge_and_inner(px)
    out = []
    if not edge:
        return out
    blacks = sum(1 for y, x in edge if tuple(px[y, x][:3]) == (0, 0, 0))
    frac = blacks / len(edge)
    if frac > 0.5:
        out.append(dict(
            check="pure_black_outline", severity=0.35,
            detail=f"{frac:.0%} of outline pixels are #000000",
            locs=[],
            hint="Consider a tinted dark outline (darkened fill hue); pure black fights "
                 "warm palettes and flattens color."))
    cov = len(edge) / max(int(a.sum()), 1)
    if cov > 0.55:
        out.append(dict(
            check="outline_heavy", severity=0.3,
            detail=f"outline is {cov:.0%} of all opaque pixels",
            locs=[],
            hint="Outline dominates: thicken fills, thin or drop interior lines."))
    return out


def check_palette(px, dup_dist=24):
    """Too many unique colors for the canvas area + near-duplicate pairs that read
    as dirty pixels."""
    a = px[..., 3] > 200
    out = []
    if not a.any():
        return out
    cols = np.unique(px[a][:, :3], axis=0)
    n = len(cols)
    area = int(a.sum())
    if area and n > 6 and n > math.sqrt(area) * 1.2:
        out.append(dict(
            check="palette_bloat", severity=0.5,
            detail=f"{n} unique colors on {area}px of art",
            locs=[],
            hint="Too many colors for this size. Group materials into ramps of 3-5 shades."))
    dups, capped = _near_duplicate_pairs(cols, dup_dist)
    if dups:
        count = f"at least {dups}" if capped else str(dups)
        out.append(dict(
            check="near_duplicate_colors", severity=0.45,
            detail=f"{count} near-identical color pairs",
            locs=[],
            hint="Near-duplicate colors read as dirty pixels. Snap them together or push "
                 "their lightness apart."))
    return out


def check_value_contrast(px, hsv_bucket=30, min_dv=18):
    """Within hue families, adjacent value stops closer than ~7% melt together at 1x.
    Use hue shift for richness instead of tiny value tweaks."""
    img8 = px.astype(np.uint8)
    hsv = np.array(Image.fromarray(img8, "RGBA").convert("HSV"), dtype=np.int32)
    a = px[..., 3] > 128
    if not a.any():
        return []
    hues = hsv[..., 0][a]
    sats = hsv[..., 1][a]
    vals = hsv[..., 2][a]
    fams = Counter()
    for hu, sa in zip(hues, sats):
        if sa > 60:
            fams[int(hu // hsv_bucket) * hsv_bucket] += 1
    weak = 0
    for f, c in fams.items():
        if c <= 40:
            continue
        sel = a & (np.abs(((hsv[..., 0] - f + 90) % 360) - 90) < 15) & (hsv[..., 1] > 60)
        if sel.sum() < 40:
            continue
        v = np.sort(np.unique(hsv[..., 2][sel]))[::-1]
        steps = np.diff(v)
        small = sum(1 for d in steps if 0 <= -d < min_dv)
        if v.size >= 3 and small >= 1:
            weak += 1
    out = []
    if weak >= 2:
        out.append(dict(
            check="weak_value_steps", severity=0.5,
            detail=f"{weak} hue families have ramp stops closer than ~7% value",
            locs=[],
            hint="Ramp steps too close in value: they melt at 1x. Space shades apart; use "
                 "hue shift, not tiny value tweaks, for richness."))
    return out


def check_silhouette(min_solidity=0.42):
    """Closure over the alpha mask; sparse silhouettes fail the black-silhouette test."""

    def run(px):
        a = px[..., 3] > 128
        if not a.any():
            return []
        ys, xs = np.where(a)
        bw = xs.max() - xs.min() + 1
        bh = ys.max() - ys.min() + 1
        solidity = float(a.sum()) / float(bw * bh)
        out = []
        if solidity < min_solidity:
            out.append(dict(
                check="silhouette_thin", severity=0.35,
                detail=f"silhouette fills only {solidity:.0%} of its bbox",
                locs=[],
                hint="Sparse silhouette: shape may be unreadable at 1x. Run the black-"
                     "silhouette test; reconnect broken masses."))
        return out

    return run


def check_dither_noise(dens_threshold=0.06):
    """Checkerboard texture density; on small canvases heavy dither reads as dirt."""

    def run(px):
        g = _srgb_to_l(px[..., :3])
        if g.size < 4:
            return []
        visible = px[..., 3] > 0
        visible_quad = (visible[:-1, :-1] & visible[:-1, 1:]
                        & visible[1:, :-1] & visible[1:, 1:])
        top_left = g[:-1, :-1]
        top_right = g[:-1, 1:]
        bottom_left = g[1:, :-1]
        bottom_right = g[1:, 1:]
        cb = (visible_quad
              & np.isclose(top_left, bottom_right)
              & np.isclose(top_right, bottom_left)
              & (np.abs(top_left - top_right) > 0.02))
        dens = float(cb.mean())
        out = []
        if dens > dens_threshold:
            out.append(dict(
                check="heavy_dither", severity=0.4,
                detail=f"checkerboard density {dens:.0%}",
                locs=[],
                hint="Heavy checkerboard texture reads as noise on small sprites. Use flat "
                     "clusters; reserve dither for gradients and transparency fades."))
        return out

    return run


CHECKS = [
    check_orphans,
    check_jaggies,
    check_banding,
    check_pillow,
    check_outline,
    check_palette,
    check_value_contrast,
    check_silhouette(),
    check_dither_noise(),
]

CHECK_NAMES = [c.__name__.replace("check_", "") for c in CHECKS]

_HINT_ORDER = ["pillow_shading", "banding", "orphan_pixels", "palette_bloat",
               "weak_value_steps", "near_duplicate_colors", "jagged_stairs",
               "heavy_dither", "pure_black_outline", "outline_heavy", "silhouette_thin"]
FINDING_NAMES = tuple(_HINT_ORDER)


def critique(path_or_image, max_hints=4, max_pixels=1_048_576):
    """Critique one sprite.

    Args:
        path_or_image: file path string OR numpy RGBA array.
        max_hints: retry hints to include (most severe first).
        max_pixels: decoded-pixel safety limit.

    Returns dict: {file, score, findings[], retry_hints[]}.
    score in [0..1], 1.0 = no findings.
    """
    if isinstance(path_or_image, str):
        px = load(path_or_image, max_pixels=max_pixels)
        name = path_or_image
    else:
        px = np.asarray(path_or_image)
        name = "<array>"
        if px.ndim != 3 or px.shape[-1] != 4:
            raise ValueError("sprite array must have shape (height, width, 4) RGBA")
        if px.shape[0] * px.shape[1] > max_pixels:
            raise ValueError(f"sprite exceeds {max_pixels} pixel limit")
        px = np.asarray(px, dtype=np.int32)
    hidden = px[..., 3] == 0
    if hidden.any():
        px = px.copy()
        px[..., :3][hidden] = 0
    findings = []
    check_failed = False
    for fn in CHECKS:
        try:
            findings.extend(fn(px))
        except Exception as e:  # a broken check must not kill the report
            check_failed = True
            findings.append(dict(
                check=fn.__name__ + "_error", severity=1.0,
                detail=f"{type(e).__name__}: critic check failed", locs=[],
                hint="Critic check failed; treat this report as invalid."))
    score = 0.0 if check_failed else max(
        0.0, 1.0 - sum(f.get("severity", 0.0) for f in findings) / 4.0)

    def rank(f):
        if f["check"].endswith("_error"):
            return -1
        try:
            return _HINT_ORDER.index(f["check"])
        except ValueError:
            return 99

    ordered = sorted(findings, key=rank)
    hints = [f["hint"] for f in ordered if f.get("hint")][:max_hints]
    return dict(file=name, score=round(score, 3),
                findings=findings, retry_hints=hints)


def critique_many(paths, max_hints=4, max_pixels=1_048_576):
    """Critique a list of paths -> {reports:[...]}. Convenience for batches."""
    reports = [critique(p, max_hints=max_hints, max_pixels=max_pixels) for p in paths]
    return dict(reports=reports)
