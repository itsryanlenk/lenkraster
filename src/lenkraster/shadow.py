"""Hash-bound, deterministic calibration runner for LenkRaster.

Shadow manifests compare bounded PNG artifacts with frozen critic and cycle
baselines.  Human art-direction examples remain explicitly human-reviewed; the
runner never promotes them to an automated approval.
"""
from __future__ import annotations

import binascii
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import zlib

import numpy as np
from PIL import Image, UnidentifiedImageError

from .critic import FINDING_NAMES, critique
from .cycle import qa_cycle


__all__ = ["ShadowValidationError", "canonical_shadow_json", "run_shadow_manifest"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CORE_PNG_CHUNKS = {b"IHDR", b"IDAT", b"IEND"}
_SAFE_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
_PATH_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_CASES = 32
_MAX_PATH_CHARS = 256
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_IMAGE_SIDE = 2048
_MAX_IMAGE_PIXELS = 1_048_576
_MAX_TOTAL_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_IMAGE_PIXELS = 8_388_608
_MAX_ARTIFACTS = 64
_MAX_CYCLE_FRAMES = 16
_MAX_CYCLE_PAIR_PIXELS = 64 * 1024 * 1024
_MAX_TOTAL_PAIR_PIXELS = 64 * 1024 * 1024
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 4096


class ShadowValidationError(ValueError):
    """A fail-closed manifest error whose message contains no filesystem path."""


def canonical_shadow_json(report):
    """Return the stable, compact JSON representation of a shadow report."""
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fail(message):
    raise ShadowValidationError(message)


def _read_bounded(path, maximum, unavailable_message):
    try:
        with path.open("rb") as stream:
            return stream.read(maximum + 1)
    except OSError:
        _fail(unavailable_message)


def _mapping(value, label):
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _exact_keys(value, required, optional, label):
    keys = set(value)
    missing = set(required) - keys
    unknown = keys - set(required) - set(optional)
    if missing or unknown:
        _fail(f"{label} has invalid fields")


def _safe_text(value, label, *, maximum=256, pattern=None):
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        _fail(f"{label} is invalid")
    if pattern is not None and pattern.fullmatch(value) is None:
        _fail(f"{label} is invalid")
    return value


def _integer(value, label, *, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{label} is invalid")
    if maximum is not None and value > maximum:
        _fail(f"{label} is invalid")
    return value


def _number(value, label, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} is invalid")
    result = float(value)
    if not math.isfinite(result):
        _fail(f"{label} is invalid")
    if minimum is not None and result < minimum:
        _fail(f"{label} is invalid")
    if maximum is not None and result > maximum:
        _fail(f"{label} is invalid")
    return result


def _relative_png(raw_path, label):
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or len(raw_path) > _MAX_PATH_CHARS
        or "\x00" in raw_path
        or "\\" in raw_path
    ):
        _fail(f"{label} must use a relative PNG path")
    path = PurePosixPath(raw_path)
    if (
        path.is_absolute()
        or len(path.parts) < 1
        or any(_PATH_COMPONENT.fullmatch(part) is None for part in path.parts)
    ):
        _fail(f"{label} must use a relative PNG path")
    if path.suffix.lower() != ".png":
        _fail(f"{label} must use a relative PNG path")
    return path


def _parse_png(raw, label, expected_width, expected_height):
    if not raw.startswith(_PNG_SIGNATURE):
        _fail(f"{label} is not a valid PNG")
    offset = len(_PNG_SIGNATURE)
    chunks = []
    idat_parts = []
    header = None
    width = height = None
    saw_iend = False
    while offset < len(raw):
        if len(raw) - offset < 12:
            _fail(f"{label} is not a valid PNG")
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(raw):
            _fail(f"{label} is not a valid PNG")
        expected_crc = struct.unpack(">I", raw[data_end:crc_end])[0]
        actual_crc = binascii.crc32(kind)
        actual_crc = binascii.crc32(raw[data_start:data_end], actual_crc) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            _fail(f"{label} is not a valid PNG")
        chunks.append(kind)
        if len(chunks) == 1:
            if kind != b"IHDR" or length != 13:
                _fail(f"{label} is not a valid PNG")
            header = raw[data_start:data_end]
            width, height = struct.unpack(">II", raw[data_start:data_start + 8])
        if kind == b"IDAT":
            idat_parts.append(raw[data_start:data_end])
        if kind == b"IEND":
            if length != 0 or crc_end != len(raw):
                _fail(f"{label} is not a valid PNG")
            saw_iend = True
            offset = crc_end
            break
        offset = crc_end
    if any(kind not in _CORE_PNG_CHUNKS for kind in chunks):
        _fail(f"{label} contains private PNG metadata")
    if (
        not saw_iend
        or header is None
        or chunks[0] != b"IHDR"
        or chunks[-1] != b"IEND"
        or chunks.count(b"IHDR") != 1
        or chunks.count(b"IEND") != 1
        or not idat_parts
        or any(kind != b"IDAT" for kind in chunks[1:-1])
    ):
        _fail(f"{label} is not a valid PNG")
    width, height, bit_depth, color_type, compression, filter_method, interlace = (
        struct.unpack(">IIBBBBB", header)
    )
    channels = {2: 3, 6: 4}.get(color_type)
    if (
        width < 1
        or height < 1
        or bit_depth != 8
        or channels is None
        or compression != 0
        or filter_method != 0
        or interlace != 0
    ):
        _fail(f"{label} is not a valid PNG")
    if (
        width > _MAX_IMAGE_SIDE
        or height > _MAX_IMAGE_SIDE
        or width * height > _MAX_IMAGE_PIXELS
    ):
        _fail(f"{label} image exceeds safety limits")
    if (width, height) != (expected_width, expected_height):
        _fail(f"{label} dimensions do not match")
    expected_decoded = height * (1 + width * channels)
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(b"".join(idat_parts), expected_decoded + 1)
        if decoder.unconsumed_tail:
            _fail(f"{label} is not a valid PNG")
        decoded += decoder.flush()
    except zlib.error:
        _fail(f"{label} is not a valid PNG")
    if (
        len(decoded) != expected_decoded
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        _fail(f"{label} is not a valid PNG")
    return width, height


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("shadow manifest contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    _fail("shadow manifest contains a non-finite JSON number")


def _parse_int(token):
    if len(token) > 32:
        _fail("shadow manifest contains an oversized numeric token")
    return int(token)


def _parse_float(token):
    if len(token) > 64:
        _fail("shadow manifest contains an oversized numeric token")
    value = float(token)
    if not math.isfinite(value):
        _fail("shadow manifest contains a non-finite JSON number")
    return value


def _bounded_json_tree(value):
    stack = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            _fail("shadow manifest JSON structure exceeds safety limits")
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


class _ArtifactValidator:
    def __init__(self, root):
        self.root = root
        self.total_bytes = 0
        self.total_pixels = 0
        self.total_pair_pixels = 0
        self.cache = {}

    def charge_pair_work(self, pixels):
        self.total_pair_pixels += pixels
        if self.total_pair_pixels > _MAX_TOTAL_PAIR_PIXELS:
            _fail("corpus cycles exceed aggregate pair-work limit")

    def validate(self, raw_artifact, case_id):
        label = f"case {case_id} artifact"
        artifact = _mapping(raw_artifact, label)
        _exact_keys(
            artifact,
            {"path", "sha256", "width", "height", "mode", "bytes"},
            set(),
            label,
        )
        relative = _relative_png(artifact["path"], label)
        expected_hash = artifact["sha256"]
        if not isinstance(expected_hash, str) or _HEX_SHA256.fullmatch(expected_hash) is None:
            _fail(f"case {case_id} has an invalid SHA-256")
        expected_width = _integer(
            artifact["width"], f"case {case_id} width", minimum=1, maximum=_MAX_IMAGE_SIDE
        )
        expected_height = _integer(
            artifact["height"], f"case {case_id} height", minimum=1, maximum=_MAX_IMAGE_SIDE
        )
        if expected_width * expected_height > _MAX_IMAGE_PIXELS:
            _fail(f"case {case_id} image exceeds safety limits")
        expected_mode = artifact["mode"]
        if expected_mode not in ("RGB", "RGBA"):
            _fail(f"case {case_id} image mode is invalid")
        expected_bytes = _integer(
            artifact["bytes"], f"case {case_id} byte size", minimum=1,
            maximum=_MAX_IMAGE_BYTES,
        )
        unresolved = self.root.joinpath(*relative.parts)
        try:
            resolved = unresolved.resolve(strict=True)
            resolved.relative_to(self.root)
        except ValueError:
            _fail(f"case {case_id} artifact is outside corpus root")
        except (OSError, RuntimeError):
            _fail(f"case {case_id} artifact is unavailable")
        if not resolved.is_file():
            _fail(f"case {case_id} artifact is unavailable")
        descriptor = {
            "path": relative.as_posix(),
            "sha256": expected_hash,
            "width": expected_width,
            "height": expected_height,
            "mode": expected_mode,
            "bytes": expected_bytes,
        }
        cached = self.cache.get(resolved)
        if cached is not None:
            if any(cached[key] != value for key, value in descriptor.items()):
                _fail(f"case {case_id} has conflicting artifact metadata")
            return cached
        raw = _read_bounded(
            resolved,
            expected_bytes,
            f"case {case_id} artifact is unavailable",
        )
        if len(raw) != expected_bytes or len(raw) > _MAX_IMAGE_BYTES:
            _fail(f"case {case_id} artifact byte size does not match")
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            _fail(f"case {case_id} artifact SHA-256 does not match")
        png_width, png_height = _parse_png(
            raw,
            f"case {case_id} artifact",
            expected_width,
            expected_height,
        )
        if (png_width, png_height) != (expected_width, expected_height):
            _fail(f"case {case_id} artifact dimensions do not match")
        if len(self.cache) >= _MAX_ARTIFACTS:
            _fail("corpus contains too many artifacts")
        self.total_bytes += len(raw)
        if self.total_bytes > _MAX_TOTAL_IMAGE_BYTES:
            _fail("corpus artifacts exceed total byte limit")
        self.total_pixels += expected_width * expected_height
        if self.total_pixels > _MAX_TOTAL_IMAGE_PIXELS:
            _fail("corpus artifacts exceed total decoded pixel limit")
        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG" or image.size != (expected_width, expected_height):
                    _fail(f"case {case_id} artifact dimensions do not match")
                if image.mode != expected_mode:
                    _fail(f"case {case_id} artifact mode does not match")
                pixels = np.array(image.convert("RGBA"), dtype=np.uint8)
        except ShadowValidationError:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
            _fail(f"case {case_id} artifact is not a valid PNG")
        record = {**descriptor, "pixels": pixels}
        self.cache[resolved] = record
        return record


def _validate_expected_critic(raw_expected, case_id):
    expected = _mapping(raw_expected, f"case {case_id} expected result")
    _exact_keys(expected, {"score", "findings"}, set(), f"case {case_id} expected result")
    score = _number(expected["score"], f"case {case_id} expected score", minimum=0, maximum=1)
    findings = expected["findings"]
    if not isinstance(findings, list) or len(findings) > 32 or any(
        not isinstance(name, str) or name not in FINDING_NAMES for name in findings
    ):
        _fail(f"case {case_id} expected findings are invalid")
    if len(set(findings)) != len(findings):
        _fail(f"case {case_id} expected findings are invalid")
    return {"score": score, "findings": findings}


def _bounds(raw, case_id):
    if not isinstance(raw, list) or len(raw) != 4:
        _fail(f"case {case_id} ROI must use xyxy-exclusive bounds")
    values = [_integer(value, f"case {case_id} ROI", minimum=0) for value in raw]
    x0, y0, x1, y1 = values
    if not (x0 < x1 and y0 < y1):
        _fail(f"case {case_id} ROI must use xyxy-exclusive bounds")
    return values


def _transition_groups(raw, case_id, frame_count):
    groups = _mapping(raw, f"case {case_id} transition groups")
    if not groups or len(groups) > frame_count * frame_count:
        _fail(f"case {case_id} transition groups are invalid")
    result = {}
    total_pairs = 0
    for name in sorted(groups):
        _safe_text(name, f"case {case_id} transition group name", maximum=64, pattern=_SAFE_ID)
        pairs = groups[name]
        if not isinstance(pairs, list) or not pairs:
            _fail(f"case {case_id} transition group is invalid")
        total_pairs += len(pairs)
        if total_pairs > frame_count * frame_count:
            _fail(f"case {case_id} transition groups exceed aggregate pair budget")
        checked = []
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                _fail(f"case {case_id} transition pair is invalid")
            left = _integer(pair[0], f"case {case_id} transition index", maximum=frame_count - 1)
            right = _integer(pair[1], f"case {case_id} transition index", maximum=frame_count - 1)
            if left == right:
                _fail(f"case {case_id} transition pair is invalid")
            checked.append([left, right])
        result[name] = checked
    return result


def _integer_list(value, case_id, label, expected_length):
    if not isinstance(value, list) or len(value) != expected_length:
        _fail(f"case {case_id} {label} is invalid")
    return [_integer(item, f"case {case_id} {label}", minimum=0) for item in value]


def _validate_expected_cycle(raw_expected, case_id, frame_count, group_names):
    expected = _mapping(raw_expected, f"case {case_id} expected result")
    _exact_keys(
        expected,
        {"verdict", "base_changed_pixels", "adjacent_changed_pixels",
         "group_verdicts", "visibility_scope"},
        set(),
        f"case {case_id} expected result",
    )
    verdict = expected["verdict"]
    if verdict not in ("PASS", "REVIEW"):
        _fail(f"case {case_id} expected verdict is invalid")
    base = _integer_list(
        expected["base_changed_pixels"], case_id, "base changed pixels", frame_count - 1
    )
    adjacent = _integer_list(
        expected["adjacent_changed_pixels"], case_id, "adjacent changed pixels",
        frame_count - 1,
    )
    group_verdicts = _mapping(expected["group_verdicts"], f"case {case_id} group verdicts")
    if set(group_verdicts) != set(group_names) or any(
        verdict_value not in ("PASS", "REVIEW") for verdict_value in group_verdicts.values()
    ):
        _fail(f"case {case_id} group verdicts are invalid")
    visibility_scope = expected["visibility_scope"]
    if visibility_scope not in {
        "frame", "roi", "not-applicable-opaque-frame",
        "not-applicable-opaque-roi",
    }:
        _fail(f"case {case_id} visibility scope is invalid")
    return {
        "verdict": verdict,
        "base_changed_pixels": base,
        "adjacent_changed_pixels": adjacent,
        "group_verdicts": {name: group_verdicts[name] for name in sorted(group_verdicts)},
        "visibility_scope": visibility_scope,
    }


def _critic_case(case, case_id, validator):
    _exact_keys(case, {"id", "kind", "artifact", "expected"}, set(), f"case {case_id}")
    artifact = validator.validate(case["artifact"], case_id)
    expected = _validate_expected_critic(case["expected"], case_id)
    result = critique(artifact["pixels"], max_pixels=_MAX_IMAGE_PIXELS)
    actual = {
        "score": result["score"],
        "findings": [finding["check"] for finding in result["findings"]],
    }
    return {
        "id": case_id,
        "kind": "critic",
        "status": "BASELINE_MATCH" if actual == expected else "DRIFT",
        "artifact": {key: artifact[key] for key in ("path", "sha256", "width", "height", "mode", "bytes")},
        "expected": expected,
        "actual": actual,
    }


def _cycle_case(case, case_id, validator):
    _exact_keys(
        case,
        {"id", "kind", "artifacts", "roi", "transition_groups", "expected"},
        set(),
        f"case {case_id}",
    )
    raw_artifacts = case["artifacts"]
    if not isinstance(raw_artifacts, list) or not 2 <= len(raw_artifacts) <= _MAX_CYCLE_FRAMES:
        _fail(f"case {case_id} must contain 2-{_MAX_CYCLE_FRAMES} frame artifacts")
    artifacts = [validator.validate(item, case_id) for item in raw_artifacts]
    dimensions = {(artifact["width"], artifact["height"]) for artifact in artifacts}
    if len(dimensions) != 1:
        _fail(f"case {case_id} frame dimensions do not match")
    width, height = next(iter(dimensions))
    roi = _bounds(case["roi"], case_id)
    if roi[2] > width or roi[3] > height:
        _fail(f"case {case_id} ROI must be inside frame dimensions")
    groups = _transition_groups(case["transition_groups"], case_id, len(artifacts))
    expected = _validate_expected_cycle(case["expected"], case_id, len(artifacts), groups)
    roi_area = (roi[2] - roi[0]) * (roi[3] - roi[1])
    pair_count = len(artifacts) * (len(artifacts) - 1) // 2
    validator.charge_pair_work(roi_area * pair_count)
    result = qa_cycle(
        [artifact["pixels"] for artifact in artifacts],
        roi=tuple(roi),
        transition_groups=groups,
        max_frames=_MAX_CYCLE_FRAMES,
        max_frame_pixels=_MAX_IMAGE_PIXELS,
        max_total_pixels=_MAX_IMAGE_PIXELS * _MAX_CYCLE_FRAMES,
        max_pair_pixels=_MAX_CYCLE_PAIR_PIXELS,
    )
    actual = {
        "verdict": result["verdict"],
        "base_changed_pixels": result["changed_pixel_matrix"][0][1:],
        "adjacent_changed_pixels": result["adjacent_changed_pixels"],
        "group_verdicts": {
            group["name"]: group["verdict"] for group in result["transition_group_reports"]
        },
        "visibility_scope": result["visibility_scope"],
    }
    return {
        "id": case_id,
        "kind": "cycle",
        "status": "BASELINE_MATCH" if actual == expected else "DRIFT",
        "artifacts": [
            {key: artifact[key] for key in ("path", "sha256", "width", "height", "mode", "bytes")}
            for artifact in artifacts
        ],
        "roi": roi,
        "transition_groups": groups,
        "expected": expected,
        "actual": actual,
    }


def _human_case(case, case_id, validator):
    _exact_keys(case, {"id", "kind", "artifact", "review"}, set(), f"case {case_id}")
    artifact = validator.validate(case["artifact"], case_id)
    review = _mapping(case["review"], f"case {case_id} review")
    _exact_keys(review, {"decision", "reason_code", "reason"}, set(), f"case {case_id} review")
    decision = review["decision"]
    if decision not in ("accepted", "rejected", "hold"):
        _fail(f"case {case_id} review decision is invalid")
    reason_code = _safe_text(
        review["reason_code"], f"case {case_id} reason code", maximum=64, pattern=_SAFE_ID
    )
    _safe_text(review["reason"], f"case {case_id} review reason", maximum=512)
    return {
        "id": case_id,
        "kind": "human_review",
        "status": "HUMAN_REVIEW",
        "artifact": {key: artifact[key] for key in ("path", "sha256", "width", "height", "mode", "bytes")},
        "decision": decision,
        "reason_code": reason_code,
    }


def run_shadow_manifest(path, manifest_sha256):
    """Validate and run a deterministic shadow calibration manifest."""
    try:
        manifest_path = Path(path).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail("shadow manifest is unavailable")
    if not manifest_path.is_file():
        _fail("shadow manifest is unavailable")
    if not isinstance(manifest_sha256, str) or _HEX_SHA256.fullmatch(manifest_sha256) is None:
        _fail("shadow manifest SHA-256 is invalid")
    raw = _read_bounded(
        manifest_path,
        _MAX_MANIFEST_BYTES,
        "shadow manifest is unavailable",
    )
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        _fail("shadow manifest exceeds safety limits")
    actual_manifest_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_manifest_sha256 != manifest_sha256:
        _fail("shadow manifest SHA-256 does not match")
    try:
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
            parse_int=_parse_int,
            parse_float=_parse_float,
        )
    except ShadowValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail("shadow manifest is not valid JSON")
    _bounded_json_tree(manifest)
    manifest = _mapping(manifest, "shadow manifest")
    _exact_keys(manifest, {"schema_version", "name", "cases"}, set(), "shadow manifest")
    if _integer(manifest["schema_version"], "shadow manifest schema", minimum=1,
                maximum=1) != 1:
        _fail("unsupported shadow manifest schema")
    name = _safe_text(manifest["name"], "shadow manifest name", maximum=96, pattern=_SAFE_ID)
    cases = manifest["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= _MAX_CASES:
        _fail("shadow manifest must contain 1-32 cases")
    root = manifest_path.parent.resolve(strict=True)
    validator = _ArtifactValidator(root)
    seen = set()
    reports = []
    for index, raw_case in enumerate(cases):
        case = _mapping(raw_case, f"case {index}")
        case_id = _safe_text(case.get("id"), f"case {index} id", maximum=64, pattern=_SAFE_ID)
        if case_id in seen:
            _fail(f"duplicate case id {case_id}")
        seen.add(case_id)
        kind = case.get("kind")
        if kind == "critic":
            reports.append(_critic_case(case, case_id, validator))
        elif kind == "cycle":
            reports.append(_cycle_case(case, case_id, validator))
        elif kind == "human_review":
            reports.append(_human_case(case, case_id, validator))
        else:
            _fail(f"case {case_id} has unknown case kind")
    automated = [case for case in reports if case["kind"] != "human_review"]
    if not automated:
        _fail("shadow manifest must contain at least one automated case")
    return {
        "schema_version": 1,
        "corpus": name,
        "manifest_sha256": actual_manifest_sha256,
        "automated_verdict": (
            "BASELINE_MATCH"
            if all(case["status"] == "BASELINE_MATCH" for case in automated)
            else "DRIFT"
        ),
        "human_review_required": any(
            case["status"] == "HUMAN_REVIEW" for case in reports
        ),
        "cases": reports,
    }
