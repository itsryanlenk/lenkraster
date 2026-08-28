"""One-shot CLI contracts."""

import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from lenkraster import cli
from lenkraster.cli import main


def _shadow_manifest(tmp_path, *, expected_score=1.0):
    image = tmp_path / "images" / "clean.png"
    image.parent.mkdir(parents=True)
    pixels = np.zeros((48, 48, 4), dtype=np.uint8)
    pixels[12:36, 12:36] = (120, 60, 40, 255)
    Image.fromarray(pixels, "RGBA").save(image)
    raw = image.read_bytes()
    manifest = {
        "schema_version": 1,
        "name": "cli-shadow-v1",
        "cases": [{
            "id": "clean-sprite",
            "kind": "critic",
            "artifact": {
                "path": "images/clean.png",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "width": 48,
                "height": 48,
                "mode": "RGBA",
                "bytes": len(raw),
            },
            "expected": {"score": expected_score, "findings": []},
        }],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _shadow_args(manifest):
    return [
        "shadow",
        str(manifest),
        "--manifest-sha256",
        hashlib.sha256(manifest.read_bytes()).hexdigest(),
    ]


def test_cycle_cli_exposes_motion_and_resource_limits(tmp_path, capsys):
    for index, x in enumerate((2, 7, 2)):
        frame = np.zeros((12, 16, 4), dtype=np.uint8)
        frame[4:8, x:x + 3] = (230, 120, 50, 255)
        Image.fromarray(frame, "RGBA").save(tmp_path / f"frame-{index}.png")

    with pytest.raises(SystemExit) as stopped:
        main([
            "cycle", str(tmp_path / "frame-*.png"),
            "--motion-threshold", "15",
            "--min-motion-pixels", "4",
            "--max-frames", "8",
            "--max-frame-pixels", "1024",
        ])

    assert stopped.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "PASS"
    assert report["loop_closure_mad"] == 0.0


def test_cycle_cli_never_truncates_json(tmp_path, capsys):
    for index in range(40):
        frame = np.zeros((3, 4, 4), dtype=np.uint8)
        frame[1, index % 4] = ((index * 17) % 255, 80, 40, 255)
        Image.fromarray(frame, "RGBA").save(tmp_path / f"frame-{index:02d}.png")

    with pytest.raises(SystemExit):
        main(["cycle", str(tmp_path / "frame-*.png")])

    report = json.loads(capsys.readouterr().out)
    assert report["frames"] == 40


def test_aseprite_export_cli_emits_canonical_path_free_manifest(
        tmp_path, capsys, monkeypatch):
    calls = []

    def export(document, out_dir, **kwargs):
        calls.append((document, out_dir, kwargs))
        return {
            "schema_version": 1,
            "kind": "aseprite-export",
            "sheet": "sheet.png",
            "frame_count": 2,
        }

    monkeypatch.setattr(cli, "export_aseprite_document", export)

    main([
        "aseprite-export",
        "sprite.aseprite",
        "--root",
        str(tmp_path),
        "--out-dir",
        "exports/walk",
        "--tag",
        "walk",
    ])

    output = capsys.readouterr().out
    assert json.loads(output)["frame_count"] == 2
    assert str(tmp_path) not in output
    assert calls == [(
        "sprite.aseprite",
        "exports/walk",
        {"trusted_root": str(tmp_path), "tag": "walk", "layer": None},
    )]


def test_aseprite_cycle_cli_preserves_advisory_exit_semantics(
        tmp_path, capsys, monkeypatch):
    calls = []

    def qa(document, **kwargs):
        calls.append((document, kwargs))
        return {"verdict": "REVIEW", "frames": 2, "issues": ["motion"]}

    monkeypatch.setattr(cli, "qa_aseprite_document", qa)

    with pytest.raises(SystemExit) as stopped:
        main([
            "aseprite-cycle",
            "sprite.ase",
            "--root",
            str(tmp_path),
            "--motion-threshold",
            "12",
            "--min-motion-pixels",
            "3",
        ])

    assert stopped.value.code == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "REVIEW"
    assert calls == [(
        "sprite.ase",
        {
            "trusted_root": str(tmp_path),
            "tag": None,
            "layer": None,
            "motion_threshold": 12.0,
            "min_motion_pixels": 3,
        },
    )]


def test_critique_cli_reports_only_basename(tmp_path, capsys):
    image = tmp_path / "private-workstation-sprite.png"
    Image.new("RGBA", (4, 4), (120, 80, 40, 255)).save(image)

    main(["critique", str(image)])

    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["file"] == image.name
    assert str(tmp_path) not in output


def test_cli_sanitizes_file_errors(tmp_path, capsys):
    missing = tmp_path / "private-workstation-path.png"

    with pytest.raises(SystemExit) as stopped:
        main(["critique", str(missing)])

    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert error == "lenkraster: command failed\n"
    assert str(tmp_path) not in error


def test_quantize_cli_accepts_user_palette_below_explicit_root(tmp_path, capsys):
    source = tmp_path / "source.png"
    output = tmp_path / "custom.png"
    palette = tmp_path / "user.json"
    Image.new("RGB", (2, 1), (120, 60, 40)).save(source)
    palette.write_text(json.dumps({
        "name": "User palette",
        "author": "Local user",
        "colors": ["000000", "ffffff"],
    }), encoding="utf-8")

    main([
        "quantize",
        str(source),
        "--palette-file",
        "user.json",
        "--root",
        str(tmp_path),
        "--out",
        str(output),
    ])

    assert output.is_file()
    assert "1/2 palette colors used" in capsys.readouterr().out


def test_quantize_cli_requires_root_for_user_palette(tmp_path, capsys):
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 1), (120, 60, 40)).save(source)

    with pytest.raises(SystemExit) as stopped:
        main(["quantize", str(source), "--palette-file", "user.json"])

    assert stopped.value.code == 2
    assert capsys.readouterr().err == "lenkraster: command failed\n"


def test_shadow_cli_emits_canonical_relative_report(tmp_path, capsys):
    manifest = _shadow_manifest(tmp_path)

    with pytest.raises(SystemExit) as stopped:
        main(_shadow_args(manifest))

    assert stopped.value.code == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert output == json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ) + "\n"
    assert report["automated_verdict"] == "BASELINE_MATCH"
    assert str(tmp_path) not in output


def test_shadow_cli_uses_review_and_invalid_exit_codes(tmp_path, capsys):
    manifest = _shadow_manifest(tmp_path, expected_score=0.5)
    with pytest.raises(SystemExit) as stopped:
        main(_shadow_args(manifest))
    assert stopped.value.code == 1
    assert json.loads(capsys.readouterr().out)["automated_verdict"] == "DRIFT"

    manifest.write_text("not JSON", encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        main(_shadow_args(manifest))
    assert stopped.value.code == 2
    assert capsys.readouterr().err == "lenkraster: command failed\n"
