"""lenkraster.cycle -- animation QA on frame sequences.

All checks are pixel math (numpy/PIL). Vision models are NOT used for QA: they flatten
GIFs to their first frame and hallucinate duplicates from thumbnails.
"""
from collections.abc import Mapping
import glob
from io import BytesIO
from itertools import islice
import math
import os
import re

import numpy as np
from PIL import Image

__all__ = ["qa_cycle", "load_frames"]


_SCHEMA_VERSION = 2
_METRIC_SCHEMA = "premultiplied-rgba-l1-changed-pixels-v1"
_BOUNDS_CONVENTION = "xyxy-exclusive"
_DEFAULT_MAX_FRAMES = 64
_DEFAULT_MAX_FRAME_PIXELS = 4_194_304
_DEFAULT_MAX_TOTAL_PIXELS = 8_388_608
_DEFAULT_MAX_PAIR_PIXELS = 33_554_432
_ABSOLUTE_MAX_FRAMES = 256
_MAX_ENCODED_FRAME_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_ENCODED_BYTES = 64 * 1024 * 1024
_GROUP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def _integer(name, value, *, minimum):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} integer")
    value = int(value)
    if value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} integer")
    return value


def _finite(name, value, *, minimum=None, maximum=None):
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and maximum is not None and not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    if minimum is not None and maximum is None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")
    return value


def _bounded_paths(pattern_or_paths, max_frames):
    if isinstance(pattern_or_paths, str):
        iterator = glob.iglob(os.path.expanduser(pattern_or_paths))
        paths = list(islice(iterator, max_frames + 1))
        if len(paths) <= max_frames:
            paths.sort()
    else:
        try:
            iterator = iter(pattern_or_paths)
        except TypeError as exc:
            raise TypeError("frames must be a glob string or iterable of paths") from exc
        paths = list(islice(iterator, max_frames + 1))
    if len(paths) > max_frames:
        raise ValueError(f"animation may contain at most {max_frames} frames")
    if not paths:
        raise FileNotFoundError("no frames matched")
    return paths


def _read_encoded_frame(path, maximum):
    try:
        with open(path, "rb") as stream:
            return stream.read(maximum + 1)
    except (OSError, TypeError):
        raise ValueError("animation frame could not be read") from None


def _load_frames(pattern_or_paths, *, dtype, max_frames, max_frame_pixels,
                 max_total_pixels):
    max_frames = _integer("max_frames", max_frames, minimum=1)
    if max_frames > _ABSOLUTE_MAX_FRAMES:
        raise ValueError(
            f"max_frames exceeds absolute safety limit of {_ABSOLUTE_MAX_FRAMES}")
    max_frame_pixels = _integer("max_frame_pixels", max_frame_pixels, minimum=1)
    max_total_pixels = _integer("max_total_pixels", max_total_pixels, minimum=1)
    paths = _bounded_paths(pattern_or_paths, max_frames)
    frames = []
    total_pixels = 0
    total_encoded_bytes = 0
    for path in paths:
        if isinstance(path, np.ndarray):
            if path.ndim != 3 or path.shape[-1] != 4 or path.dtype != np.uint8:
                raise ValueError("in-memory animation frames must be uint8 RGBA arrays")
            height, width = path.shape[:2]
            image = None
        else:
            remaining_encoded_bytes = _MAX_TOTAL_ENCODED_BYTES - total_encoded_bytes
            read_limit = min(_MAX_ENCODED_FRAME_BYTES, remaining_encoded_bytes)
            raw = _read_encoded_frame(path, read_limit)
            if len(raw) > read_limit:
                if remaining_encoded_bytes < _MAX_ENCODED_FRAME_BYTES:
                    raise ValueError(
                        f"animation exceeds {_MAX_TOTAL_ENCODED_BYTES} "
                        "total encoded byte limit")
                raise ValueError(
                    f"animation frame exceeds {_MAX_ENCODED_FRAME_BYTES} "
                    "encoded byte limit")
            total_encoded_bytes += len(raw)
            try:
                image = Image.open(BytesIO(raw))
            except (Image.DecompressionBombError, OSError, ValueError):
                raise ValueError("animation frame is not a valid image") from None
            width, height = image.size
        frame_pixels = width * height
        if frame_pixels > max_frame_pixels:
            if image is not None:
                image.close()
            raise ValueError(f"animation frame exceeds {max_frame_pixels} pixel limit")
        total_pixels += frame_pixels
        if total_pixels > max_total_pixels:
            if image is not None:
                image.close()
            raise ValueError(
                f"animation exceeds {max_total_pixels} total decoded pixel limit")
        if image is None:
            frame = np.array(path, dtype=dtype, copy=True)
        else:
            try:
                frame = np.array(image.convert("RGBA"), dtype=dtype)
            except (OSError, ValueError):
                raise ValueError("animation frame is not a valid image") from None
            finally:
                image.close()
        frames.append(frame)
    shapes = {frame.shape for frame in frames}
    if len(shapes) != 1:
        raise ValueError("all animation frames must have the same dimensions")
    return frames, paths


def load_frames(pattern_or_paths, *, max_frames=_DEFAULT_MAX_FRAMES,
                max_frame_pixels=_DEFAULT_MAX_FRAME_PIXELS,
                max_total_pixels=_DEFAULT_MAX_TOTAL_PIXELS):
    """Load bounded RGBA frames, preserving the public int32 array contract."""
    return _load_frames(
        pattern_or_paths,
        dtype=np.int32,
        max_frames=max_frames,
        max_frame_pixels=max_frame_pixels,
        max_total_pixels=max_total_pixels,
    )


def _visible(frame):
    return frame[..., 3] > 128


def _rendered_rgba(frame):
    """Premultiplied RGBA scaled by 255, independent of hidden transparent RGB."""
    rgba = frame.astype(np.uint16, copy=False)
    alpha = rgba[..., 3:4]
    premultiplied = rgba[..., :3] * alpha
    scaled_alpha = alpha * np.uint16(255)
    return np.concatenate((premultiplied, scaled_alpha), axis=-1)


def _pair_change_mask(rendered_a, rendered_b):
    return np.any(rendered_a != rendered_b, axis=-1)


def _pair_stats(rendered_a, rendered_b):
    """Mean premultiplied-RGBA L1 delta and count over rendered pixels that change."""
    changed = _pair_change_mask(rendered_a, rendered_b)
    count = int(changed.sum())
    if not count:
        return 0.0, 0
    total_delta = 0
    for channel in range(4):
        left = rendered_a[..., channel][changed].astype(np.int32)
        right = rendered_b[..., channel][changed].astype(np.int32)
        total_delta += int(np.abs(left - right).sum())
    return total_delta / (255.0 * count), count


def _active_bounds(rendered_frames):
    """Return exclusive local pixel bounds that change anywhere in the sequence."""
    first = rendered_frames[0]
    changed = np.zeros(first.shape[:2], dtype=bool)
    for frame in rendered_frames[1:]:
        changed |= _pair_change_mask(first, frame)
    if not changed.any():
        return None
    ys, xs = np.where(changed)
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def _safe_label(path, index):
    if isinstance(path, np.ndarray):
        return f"frame-{index}"
    label = str(os.fspath(path)).replace("\\", "/").rsplit("/", 1)[-1]
    return label or f"frame-{index}"


def _normalize_roi(roi, width, height):
    if roi is None:
        return (0, 0, width, height), False
    try:
        values = tuple(roi)
    except TypeError as exc:
        raise ValueError("roi must contain four integer xyxy-exclusive bounds") from exc
    if len(values) != 4 or any(
            isinstance(value, bool) or not isinstance(value, (int, np.integer))
            for value in values):
        raise ValueError("roi must contain four integer xyxy-exclusive bounds")
    x0, y0, x1, y1 = (int(value) for value in values)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("roi must be inside frame dimensions using xyxy-exclusive bounds")
    return (x0, y0, x1, y1), True


def _normalize_transition_groups(transition_groups, frame_count):
    if transition_groups is None:
        pairs = [(index, index + 1) for index in range(frame_count - 1)]
        return [("cycle", pairs)] if pairs else []
    if not isinstance(transition_groups, Mapping) or not transition_groups:
        raise ValueError("transition_groups must be a non-empty mapping")
    if len(transition_groups) > max(1, frame_count * frame_count):
        raise ValueError("transition_groups contains too many groups")
    names = list(transition_groups)
    if any(not isinstance(name, str) or not _GROUP_NAME.fullmatch(name) for name in names):
        raise ValueError("transition group names must be safe 1-64 character labels")
    normalized = []
    aggregate_pairs = 0
    aggregate_pair_budget = frame_count * frame_count
    for name in sorted(names):
        remaining_pair_budget = aggregate_pair_budget - aggregate_pairs
        try:
            pairs = list(islice(iter(transition_groups[name]), remaining_pair_budget + 1))
        except TypeError as exc:
            raise ValueError(f"transition group {name!r} must contain frame-index pairs") from exc
        if not pairs:
            raise ValueError(f"transition group {name!r} must contain frame-index pairs")
        if len(pairs) > remaining_pair_budget:
            raise ValueError("transition_groups exceeds aggregate pair budget")
        aggregate_pairs += len(pairs)
        checked = set()
        for pair in pairs:
            try:
                indices = tuple(pair)
            except TypeError as exc:
                raise ValueError(
                    f"transition group {name!r} has an invalid frame-index pair") from exc
            if len(indices) != 2 or any(
                    isinstance(index, bool) or not isinstance(index, (int, np.integer))
                    for index in indices):
                raise ValueError(f"transition group {name!r} has an invalid frame-index pair")
            left, right = (int(index) for index in indices)
            if left == right or not (0 <= left < frame_count and 0 <= right < frame_count):
                raise ValueError(f"transition group {name!r} frame index is out of range")
            checked.add((left, right))
        normalized.append((name, sorted(checked)))
    return normalized


def qa_cycle(frames_or_pattern, flicker_ratio=0.5, motion_threshold=15.0,
             min_motion_pixels=4, max_frames=_DEFAULT_MAX_FRAMES,
             max_frame_pixels=_DEFAULT_MAX_FRAME_PIXELS,
             max_total_pixels=_DEFAULT_MAX_TOTAL_PIXELS,
             max_pair_pixels=_DEFAULT_MAX_PAIR_PIXELS, roi=None,
             transition_groups=None):
    """QA one animation cycle with bounded, rendered-pixel comparisons.

    ``roi`` uses ``(x0, y0, x1, y1)`` exclusive bounds and scopes visibility/flicker.
    ``transition_groups`` maps safe labels to explicit ``(from_frame, to_frame)`` pairs;
    every group must contain at least one transition meeting both motion thresholds.
    """
    flicker_ratio = _finite("flicker_ratio", flicker_ratio, minimum=0.0, maximum=1.0)
    motion_threshold = _finite("motion_threshold", motion_threshold, minimum=0.0)
    if motion_threshold == 0.0:
        raise ValueError("motion_threshold must be positive")
    min_motion_pixels = _integer("min_motion_pixels", min_motion_pixels, minimum=1)
    max_pair_pixels = _integer("max_pair_pixels", max_pair_pixels, minimum=1)
    frames, names = _load_frames(
        frames_or_pattern,
        dtype=np.uint8,
        max_frames=max_frames,
        max_frame_pixels=max_frame_pixels,
        max_total_pixels=max_total_pixels,
    )
    frame_count = len(frames)
    if frame_count < 2:
        raise ValueError("cycle QA requires at least two frames")
    height, width = frames[0].shape[:2]
    analysis_roi, has_roi = _normalize_roi(roi, width, height)
    x0, y0, x1, y1 = analysis_roi
    scoped_frames = [frame[y0:y1, x0:x1] for frame in frames]
    rendered_frames = [_rendered_rgba(frame) for frame in scoped_frames]
    local_bounds = _active_bounds(rendered_frames)
    if local_bounds is None:
        active_frames = rendered_frames
        active_bounds = None
        active_area = 0
    else:
        bx0, by0, bx1, by1 = local_bounds
        active_frames = [frame[by0:by1, bx0:bx1] for frame in rendered_frames]
        active_bounds = [x0 + bx0, y0 + by0, x0 + bx1, y0 + by1]
        active_area = (bx1 - bx0) * (by1 - by0)
    pair_count = frame_count * (frame_count - 1) // 2
    pair_work = active_area * pair_count
    if pair_work > max_pair_pixels:
        raise ValueError(f"animation exceeds {max_pair_pixels} pair-work pixel limit")

    raw_identity = [[0.0] * frame_count for _ in range(frame_count)]
    changed_matrix = [[0] * frame_count for _ in range(frame_count)]
    if local_bounds is not None:
        for left in range(frame_count):
            for right in range(left + 1, frame_count):
                mad, changed = _pair_stats(active_frames[left], active_frames[right])
                raw_identity[left][right] = raw_identity[right][left] = mad
                changed_matrix[left][right] = changed_matrix[right][left] = changed
    identity = [[round(value, 1) for value in row] for row in raw_identity]
    labels = [_safe_label(path, index) for index, path in enumerate(names)]
    visible_masks = [_visible(frame) for frame in scoped_frames]
    visible_counts = [int(mask.sum()) for mask in visible_masks]
    fully_opaque = all(bool(mask.all()) for mask in visible_masks)
    if fully_opaque:
        visibility_ratio = None
        visibility_scope = f"not-applicable-opaque-{'roi' if has_roi else 'frame'}"
    else:
        visibility_ratio = (
            min(visible_counts) / max(visible_counts) if max(visible_counts) else 0.0)
        visibility_scope = "roi" if has_roi else "frame"
    adjacent_pairs = [(index, index + 1) for index in range(frame_count - 1)]
    adjacent_raw = [raw_identity[left][right] for left, right in adjacent_pairs]
    adjacent_mad = [round(value, 1) for value in adjacent_raw]
    adjacent_pixels = [changed_matrix[left][right] for left, right in adjacent_pairs]
    motion_peak_mad = max(adjacent_raw, default=0.0)
    motion_peak_pixels = max(adjacent_pixels, default=0)
    closure = raw_identity[0][frame_count - 1] if frame_count > 1 else 0.0

    issues = []
    if visibility_ratio is not None and visibility_ratio < flicker_ratio:
        minimum = min(visible_counts)
        low_frames = [f"{index}:{labels[index]}" for index, count in enumerate(visible_counts)
                      if count == minimum]
        issues.append(
            f"visible-pixel min/max {visibility_ratio:.2f} < {flicker_ratio}: "
            f"flicker/empty frames at {low_frames}")

    normalized_groups = _normalize_transition_groups(transition_groups, frame_count)
    group_reports = []
    for name, pairs in normalized_groups:
        transitions = []
        qualifying = []
        for left, right in pairs:
            mad = raw_identity[left][right]
            pixels = changed_matrix[left][right]
            qualifies = mad >= motion_threshold and pixels >= min_motion_pixels
            transitions.append({
                "from": left,
                "to": right,
                "mad": round(mad, 1),
                "changed_pixels": pixels,
                "qualifies": qualifies,
            })
            if qualifies:
                qualifying.append([left, right])
        verdict = "PASS" if qualifying else "REVIEW"
        group_reports.append({
            "name": name,
            "verdict": verdict,
            "transitions": transitions,
            "qualifying_transitions": qualifying,
        })
        if verdict == "REVIEW":
            issues.append(
                f"motion group {name!r} does not move: no transition meets MAD >= "
                f"{motion_threshold:g} and changed pixels >= {min_motion_pixels}")

    return {
        "schema_version": _SCHEMA_VERSION,
        "metric_schema": _METRIC_SCHEMA,
        "bounds_convention": _BOUNDS_CONVENTION,
        "verdict": "PASS" if not issues else "REVIEW",
        "frames": frame_count,
        "frame_names": labels,
        "visibility_scope": visibility_scope,
        "roi": list(analysis_roi) if has_roi else None,
        "visible_counts": visible_counts,
        "visibility_ratio": (round(visibility_ratio, 3)
                             if visibility_ratio is not None else None),
        "identity_matrix": identity,
        "changed_pixel_matrix": changed_matrix,
        "active_bounds": active_bounds,
        "adjacent_mad": adjacent_mad,
        "adjacent_changed_pixels": adjacent_pixels,
        "motion_peak_mad": round(motion_peak_mad, 1),
        "motion_peak_pixels": motion_peak_pixels,
        "loop_closure_mad": round(closure, 1),
        "first_last_mad": round(closure, 1),
        "transition_group_reports": group_reports,
        "issues": issues,
        "hints": (["Fix: regenerate flagged frames with the same prompt plus 'consistent "
                   "size, same palette, same viewing angle; never mirror between frames'."]
                  if issues else []),
    }
