"""Manifest-driven shadow-mode calibration tests."""
import hashlib
import binascii
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np
from PIL import Image, PngImagePlugin
import pytest

import lenkraster.shadow as shadow
from lenkraster.shadow import (
    ShadowValidationError,
    canonical_shadow_json,
    run_shadow_manifest as _run_shadow_manifest,
)


def run_shadow_manifest(path):
    raw = path.read_bytes()
    return _run_shadow_manifest(path, hashlib.sha256(raw).hexdigest())


def _write_png(path, pixels, *, text=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    info = None
    if text is not None:
        info = PngImagePlugin.PngInfo()
        info.add_text("private-source", text)
    Image.fromarray(pixels).save(path, pnginfo=info)
    return path


def _artifact(root, path):
    raw = path.read_bytes()
    with Image.open(path) as image:
        width, height = image.size
        mode = image.mode
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "width": width,
        "height": height,
        "mode": mode,
        "bytes": len(raw),
    }


def _rewrite_png(path, transform):
    raw = path.read_bytes()
    path.write_bytes(transform(raw))
    return path


def _duplicate_ihdr(raw):
    ihdr_end = 8 + 4 + 4 + 13 + 4
    return raw[:ihdr_end] + raw[8:ihdr_end] + raw[ihdr_end:]


def _replace_ihdr_dimensions(raw, width, height):
    data_start = 8 + 4 + 4
    data_end = data_start + 13
    header = struct.pack(">II", width, height) + raw[data_start + 8:data_end]
    body = b"IHDR" + header
    return (
        raw[:8]
        + struct.pack(">I", len(header))
        + body
        + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
        + raw[data_end + 4:]
    )


def _append_first_idat_payload(raw, payload):
    offset = 8
    while offset < len(raw):
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if kind == b"IDAT":
            data = raw[data_start:data_end] + payload
            body = kind + data
            chunk = struct.pack(">I", len(data)) + body
            chunk += struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
            return raw[:offset] + chunk + raw[crc_end:]
        offset = crc_end
    raise AssertionError("fixture has no IDAT")


def _clean_sprite():
    size = 48
    image = np.zeros((size, size, 4), dtype=np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    distance = np.sqrt((yy - size // 2) ** 2 + (xx - size // 2) ** 2)
    body = distance < 18
    highlight = (distance < 10) & (xx < size // 2)
    image[body] = (120, 60, 40, 255)
    image[highlight] = (210, 130, 80, 255)
    return image


def _cycle_frames():
    base = np.full((8, 8, 4), (80, 60, 40, 255), dtype=np.uint8)
    frames = [base]
    first = base.copy()
    first[1:3, 1:3, :3] = (240, 30, 20)
    frames.append(first)
    second = base.copy()
    second[1:3, 1:3, :3] = (20, 220, 30)
    frames.append(second)
    third = base.copy()
    third[5:7, 5:7, :3] = (20, 30, 240)
    frames.append(third)
    fourth = base.copy()
    fourth[5:7, 5:7, :3] = (240, 240, 240)
    frames.append(fourth)
    return frames


def _valid_manifest(tmp_path):
    critic_path = _write_png(tmp_path / "images" / "clean.png", _clean_sprite())
    human_path = _write_png(
        tmp_path / "images" / "rejected-sand-mat.png",
        np.full((8, 8, 3), (184, 146, 104), dtype=np.uint8),
    )
    frame_paths = []
    for index, frame in enumerate(_cycle_frames()):
        frame_paths.append(_write_png(
            tmp_path / "motion" / f"frame-{index}.png", frame
        ))
    return {
        "schema_version": 1,
        "name": "synthetic-shadow-v1",
        "cases": [
            {
                "id": "accepted-clean-sprite",
                "kind": "critic",
                "artifact": _artifact(tmp_path, critic_path),
                "expected": {"score": 1.0, "findings": []},
            },
            {
                "id": "accepted-motion",
                "kind": "cycle",
                "artifacts": [_artifact(tmp_path, path) for path in frame_paths],
                "roi": [0, 0, 8, 8],
                "transition_groups": {
                    "pincer-flex": [[0, 1], [1, 2]],
                    "walking-leg-settle": [[0, 3], [3, 4]],
                },
                "expected": {
                    "verdict": "PASS",
                    "base_changed_pixels": [4, 4, 4, 4],
                    "adjacent_changed_pixels": [4, 4, 8, 4],
                    "group_verdicts": {
                        "pincer-flex": "PASS",
                        "walking-leg-settle": "PASS",
                    },
                    "visibility_scope": "not-applicable-opaque-roi",
                },
            },
            {
                "id": "rejected-scrapbook-support",
                "kind": "human_review",
                "artifact": _artifact(tmp_path, human_path),
                "review": {
                    "decision": "rejected",
                    "reason_code": "scrapbook-sand-mat",
                    "reason": "Separate support tile reads as pasted over the beach.",
                },
            },
        ],
    }


def _write_manifest(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def test_shadow_manifest_runs_automated_cases_and_preserves_human_review(tmp_path):
    path = _write_manifest(tmp_path, _valid_manifest(tmp_path))

    report = run_shadow_manifest(path)

    assert report["automated_verdict"] == "BASELINE_MATCH"
    assert report["human_review_required"] is True
    assert [case["status"] for case in report["cases"]] == [
        "BASELINE_MATCH", "BASELINE_MATCH", "HUMAN_REVIEW"
    ]
    assert report["cases"][2]["decision"] == "rejected"
    assert str(tmp_path) not in canonical_shadow_json(report)


def test_shadow_report_is_byte_deterministic(tmp_path):
    path = _write_manifest(tmp_path, _valid_manifest(tmp_path))

    outputs = [canonical_shadow_json(run_shadow_manifest(path)) for _ in range(3)]

    assert outputs[0] == outputs[1] == outputs[2]


def test_shadow_report_is_byte_deterministic_across_processes(tmp_path):
    path = _write_manifest(tmp_path, _valid_manifest(tmp_path))
    pinned = hashlib.sha256(path.read_bytes()).hexdigest()
    command = [
        sys.executable,
        "-m",
        "lenkraster.cli",
        "shadow",
        str(path),
        "--manifest-sha256",
        pinned,
    ]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    runs = [
        subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        for _ in range(3)
    ]

    assert [run.returncode for run in runs] == [0, 0, 0]
    assert runs[0].stdout == runs[1].stdout == runs[2].stdout
    assert all(run.stderr == "" for run in runs)
    assert str(tmp_path) not in runs[0].stdout


def test_shadow_manifest_reports_baseline_regression_without_claiming_pass(tmp_path):
    manifest = _valid_manifest(tmp_path)
    manifest["cases"][0]["expected"]["score"] = 0.5
    path = _write_manifest(tmp_path, manifest)

    report = run_shadow_manifest(path)

    assert report["automated_verdict"] == "DRIFT"
    assert report["cases"][0]["status"] == "DRIFT"
    assert report["human_review_required"] is True


def test_shadow_manifest_accepts_real_underscore_check_names(tmp_path):
    manifest = _valid_manifest(tmp_path)
    black = _write_png(
        tmp_path / "images" / "black.png",
        np.full((16, 16, 4), (0, 0, 0, 255), dtype=np.uint8),
    )
    manifest["cases"][0]["artifact"] = _artifact(tmp_path, black)
    manifest["cases"][0]["expected"] = {
        "score": 0.912,
        "findings": ["pure_black_outline"],
    }

    report = run_shadow_manifest(_write_manifest(tmp_path, manifest))

    assert report["cases"][0]["status"] == "BASELINE_MATCH"


def test_shadow_manifest_rejects_stale_hash_and_traversal(tmp_path):
    manifest = _valid_manifest(tmp_path)
    manifest["cases"][0]["artifact"]["sha256"] = "0" * 64
    path = _write_manifest(tmp_path, manifest)
    with pytest.raises(ShadowValidationError, match="accepted-clean-sprite"):
        run_shadow_manifest(path)

    manifest = _valid_manifest(tmp_path)
    manifest["cases"][0]["artifact"]["path"] = "../outside.png"
    path = _write_manifest(tmp_path, manifest)
    with pytest.raises(ShadowValidationError, match="relative PNG"):
        run_shadow_manifest(path)


def test_shadow_manifest_rejects_oversized_manifest_before_unbounded_read(
    tmp_path, monkeypatch
):
    path = tmp_path / "oversized-manifest.json"
    with path.open("wb") as stream:
        stream.seek(shadow._MAX_MANIFEST_BYTES)
        stream.write(b"\0")

    def unbounded_read_was_used(_path):
        raise AssertionError("oversized manifest reached Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", unbounded_read_was_used)

    with pytest.raises(ShadowValidationError, match="exceeds safety limits"):
        _run_shadow_manifest(path, "0" * 64)


def test_shadow_manifest_rejects_oversized_artifact_before_unbounded_read(
    tmp_path, monkeypatch
):
    manifest = _valid_manifest(tmp_path)
    artifact = manifest["cases"][0]["artifact"]
    source = tmp_path / artifact["path"]
    with source.open("wb") as stream:
        stream.seek(shadow._MAX_IMAGE_BYTES)
        stream.write(b"\0")
    artifact["bytes"] = shadow._MAX_IMAGE_BYTES
    artifact["sha256"] = "0" * 64
    path = _write_manifest(tmp_path, manifest)
    pinned = hashlib.sha256(path.read_bytes()).hexdigest()
    source = source.resolve()
    original_read_bytes = Path.read_bytes

    def reject_artifact_read_bytes(candidate):
        if candidate.resolve() == source:
            raise AssertionError("oversized artifact reached Path.read_bytes")
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", reject_artifact_read_bytes)

    with pytest.raises(ShadowValidationError, match="artifact byte size does not match"):
        _run_shadow_manifest(path, pinned)


def test_shadow_manifest_rejects_duplicates_unknown_kinds_and_metadata(tmp_path):
    manifest = _valid_manifest(tmp_path)
    manifest["cases"][1]["id"] = manifest["cases"][0]["id"]
    with pytest.raises(ShadowValidationError, match="duplicate case id"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))

    manifest = _valid_manifest(tmp_path)
    manifest["cases"][0]["kind"] = "automatic-art-approval"
    with pytest.raises(ShadowValidationError, match="unknown case kind"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))

    manifest = _valid_manifest(tmp_path)
    tainted = _write_png(
        tmp_path / "images" / "tainted.png",
        _clean_sprite(),
        text="private-source://do-not-publish",
    )
    manifest["cases"][0]["artifact"] = _artifact(tmp_path, tainted)
    with pytest.raises(ShadowValidationError, match="private PNG metadata"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))


@pytest.mark.parametrize("mutation", [_duplicate_ihdr, lambda raw: _append_first_idat_payload(
    raw, b"sentinel@redaction.invalid Z:/redaction-sentinel/Private")])
def test_shadow_manifest_rejects_ambiguous_png_core_streams(tmp_path, mutation):
    manifest = _valid_manifest(tmp_path)
    source = tmp_path / manifest["cases"][0]["artifact"]["path"]
    _rewrite_png(source, mutation)
    manifest["cases"][0]["artifact"] = _artifact(tmp_path, source)

    with pytest.raises(ShadowValidationError, match="valid PNG"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))


@pytest.mark.parametrize(
    ("actual_width", "message"),
    [
        (shadow._MAX_IMAGE_SIDE + 1, "image exceeds safety limits"),
        (49, "dimensions do not match"),
    ],
)
def test_shadow_manifest_rejects_untrusted_ihdr_before_decompression(
    tmp_path, monkeypatch, actual_width, message
):
    manifest = _valid_manifest(tmp_path)
    artifact = manifest["cases"][0]["artifact"]
    source = tmp_path / artifact["path"]
    raw = _replace_ihdr_dimensions(
        source.read_bytes(), actual_width, artifact["height"]
    )
    source.write_bytes(raw)
    artifact["sha256"] = hashlib.sha256(raw).hexdigest()
    artifact["bytes"] = len(raw)

    def unexpected_decoder_call():
        raise AssertionError("oversized IHDR reached zlib")

    monkeypatch.setattr(shadow.zlib, "decompressobj", unexpected_decoder_call)

    with pytest.raises(ShadowValidationError, match=message):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))


def test_shadow_manifest_rejects_symlink_escape_when_supported(tmp_path):
    outside = _write_png(tmp_path.parent / "outside.png", _clean_sprite())
    link = tmp_path / "images" / "linked.png"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("platform account cannot create symlinks")
    manifest = _valid_manifest(tmp_path)
    manifest["cases"][0]["artifact"] = {
        **_artifact(tmp_path.parent, outside),
        "path": "images/linked.png",
    }

    with pytest.raises(ShadowValidationError, match="outside corpus root"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))


def test_shadow_manifest_rejects_duplicate_json_keys_and_nonfinite_numbers(tmp_path):
    manifest = _valid_manifest(tmp_path)
    raw = json.dumps(manifest)
    duplicate = raw.replace(
        '"schema_version": 1,',
        '"schema_version": 1, "schema_version": 1,',
        1,
    )
    path = tmp_path / "manifest.json"
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ShadowValidationError, match="duplicate JSON key"):
        run_shadow_manifest(path)

    manifest["cases"][0]["expected"]["score"] = float("nan")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ShadowValidationError, match="non-finite JSON number"):
        run_shadow_manifest(path)

    huge = '{"schema_version":' + ("9" * 5000) + ',"name":"x","cases":[]}'
    path.write_text(huge, encoding="utf-8")
    with pytest.raises(ShadowValidationError, match="numeric token"):
        run_shadow_manifest(path)


def test_shadow_manifest_requires_a_caller_pinned_sha256(tmp_path):
    path = _write_manifest(tmp_path, _valid_manifest(tmp_path))

    with pytest.raises(ShadowValidationError, match="manifest SHA-256"):
        _run_shadow_manifest(path, "0" * 64)
    with pytest.raises(ShadowValidationError, match="manifest SHA-256"):
        _run_shadow_manifest(path, "not-a-hash")


def test_shadow_manifest_requires_strict_version_and_an_automated_case(tmp_path):
    manifest = _valid_manifest(tmp_path)
    manifest["schema_version"] = True
    with pytest.raises(ShadowValidationError, match="schema"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))

    manifest = _valid_manifest(tmp_path)
    manifest["cases"] = [manifest["cases"][2]]
    with pytest.raises(ShadowValidationError, match="automated case"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))


def test_shadow_report_never_copies_free_text_or_windows_like_paths(tmp_path):
    manifest = _valid_manifest(tmp_path)
    manifest["cases"][2]["review"]["reason"] = (
        "Contact sentinel@redaction.invalid at Z:/redaction-sentinel/Private/Desktop"
    )
    report = run_shadow_manifest(_write_manifest(tmp_path, manifest))
    serialized = canonical_shadow_json(report)
    assert "sentinel@redaction.invalid" not in serialized
    assert "Z:/redaction-sentinel" not in serialized

    manifest = _valid_manifest(tmp_path)
    manifest["cases"][0]["artifact"]["path"] = "Z:/redaction-sentinel/private.png"
    with pytest.raises(ShadowValidationError, match="relative PNG"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))

    manifest = _valid_manifest(tmp_path)
    manifest["cases"][1]["expected"]["visibility_scope"] = (
        "sentinel@redaction.invalid Z:/redaction-sentinel/Private"
    )
    with pytest.raises(ShadowValidationError, match="visibility scope"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))


def test_shadow_manifest_charges_unique_decoded_pixels_globally(tmp_path, monkeypatch):
    manifest = _valid_manifest(tmp_path)
    monkeypatch.setattr(shadow, "_MAX_TOTAL_IMAGE_PIXELS", 100)

    with pytest.raises(ShadowValidationError, match="decoded pixel limit"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))


def test_shadow_manifest_decodes_repeated_artifact_only_once(tmp_path, monkeypatch):
    manifest = _valid_manifest(tmp_path)
    manifest["cases"] = [manifest["cases"][0], manifest["cases"][2]]
    manifest["cases"][1]["artifact"] = dict(manifest["cases"][0]["artifact"])
    calls = 0
    original = shadow.Image.open

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(shadow.Image, "open", counted)
    run_shadow_manifest(_write_manifest(tmp_path, manifest))

    assert calls == 1


def test_shadow_manifest_charges_cycle_pair_work_globally(tmp_path, monkeypatch):
    manifest = _valid_manifest(tmp_path)
    monkeypatch.setattr(shadow, "_MAX_TOTAL_PAIR_PIXELS", 100)

    with pytest.raises(ShadowValidationError, match="pair-work"):
        run_shadow_manifest(_write_manifest(tmp_path, manifest))
