"""Behavioral contracts for the GUI-facing LenkRaster service facade."""

import json
from pathlib import Path

import pytest
from PIL import Image

from lenkraster import studio


def _save_png(path, *, size=(4, 4), color=(91, 110, 225, 255)):
    Image.new("RGBA", size, color).save(path)
    return path


def _public_text(value):
    return json.dumps(value, default=str)


def test_inspect_sprite_is_bounded_and_does_not_publish_an_absolute_path(
    tmp_path, monkeypatch
):
    source = _save_png(tmp_path / "hero.png")
    calls = []

    def critique(path, **kwargs):
        calls.append(("critique", Path(path), kwargs))
        return {
            "file": str(source.resolve()),
            "score": 0.875,
            "findings": [],
            "retry_hints": [],
        }

    def contrast(path, **kwargs):
        calls.append(("contrast", Path(path), kwargs))
        return {"summary": "all steps are separated", "weak": []}

    monkeypatch.setattr(studio, "critique", critique)
    monkeypatch.setattr(studio, "check_contrast", contrast)

    report = studio.inspect_sprite(
        "hero.png",
        trusted_root=tmp_path,
        max_pixels=321,
    )

    assert [(kind, path.resolve()) for kind, path, _kwargs in calls] == [
        ("critique", source.resolve()),
        ("contrast", source.resolve()),
    ]
    assert [kwargs["max_pixels"] for _kind, _path, kwargs in calls] == [321, 321]
    assert report["file"] == "hero.png"
    assert report["critique"]["file"] == "hero.png"
    assert report["contrast"]["summary"] == "all steps are separated"
    assert str(tmp_path) not in _public_text(report)


def test_quantized_preview_is_in_memory_and_leaves_no_output_file(tmp_path):
    source = _save_png(
        tmp_path / "source.png",
        size=(3, 2),
        color=(118, 66, 138, 180),
    )
    before = {path.name for path in tmp_path.iterdir()}

    preview = studio.preview_quantization(
        source.name,
        "lenk-fern-4",
        trusted_root=tmp_path,
        max_pixels=6,
    )

    assert isinstance(preview["image"], Image.Image)
    assert preview["image"].size == (3, 2)
    assert preview["image"].mode == "RGBA"
    assert preview["palette"] == "lenk-fern-4"
    assert 1 <= preview["colors_used"] <= 4
    assert preview["used_colors"]
    assert {path.name for path in tmp_path.iterdir()} == before


def test_quantized_png_export_is_create_only_and_preserves_a_collision(tmp_path):
    _save_png(tmp_path / "source.png", color=(225, 77, 81, 255))

    result = studio.export_quantized_png(
        "source.png",
        "lenk-signal-16",
        "quantized.png",
        trusted_root=tmp_path,
        max_pixels=16,
    )

    assert result["file"] == "quantized.png"
    assert result["colors_used"] >= 1
    with Image.open(tmp_path / "quantized.png") as exported:
        exported.verify()

    collision = tmp_path / "existing.png"
    sentinel = b"existing user content must survive"
    collision.write_bytes(sentinel)

    with pytest.raises(studio.StudioError, match=r"^output already exists$"):
        studio.export_quantized_png(
            "source.png",
            "lenk-signal-16",
            collision.name,
            trusted_root=tmp_path,
        )

    assert collision.read_bytes() == sentinel


def test_user_palette_preview_and_export_stay_inside_the_workspace(tmp_path):
    _save_png(tmp_path / "source.png", color=(225, 77, 81, 255))
    (tmp_path / "my-palette.json").write_text(
        json.dumps({
            "name": "My palette",
            "author": "Workspace owner",
            "colors": ["000000", "ff4d51", "ffffff"],
        }),
        encoding="utf-8",
    )

    loaded = studio.load_user_palette(
        "my-palette.json",
        trusted_root=tmp_path,
    )
    preview = studio.preview_quantization(
        "source.png",
        None,
        trusted_root=tmp_path,
        palette_file="my-palette.json",
    )
    exported = studio.export_quantized_png(
        "source.png",
        None,
        "custom.png",
        trusted_root=tmp_path,
        palette_file="my-palette.json",
    )

    assert loaded == {
        "file": "my-palette.json",
        "colors": ["#000000", "#ff4d51", "#ffffff"],
        "metadata": {"name": "My palette", "author": "Workspace owner"},
    }
    assert preview["palette"] == "my-palette.json"
    assert preview["colors_used"] >= 1
    assert exported["file"] == "custom.png"
    assert str(tmp_path) not in _public_text((loaded, preview["palette"], exported))
    with Image.open(tmp_path / "custom.png") as image:
        image.verify()


def test_ramp_generation_and_create_only_png_export(tmp_path):
    ramp = studio.build_ramp("#5b6ee1", stops=4, drift=-8.0)

    assert len(ramp) == 4
    assert all(color.startswith("#") and len(color) == 7 for color in ramp)

    result = studio.export_ramp_png(
        "#5b6ee1",
        "ramp.png",
        trusted_root=tmp_path,
        stops=4,
        drift=-8.0,
        swatch_size=3,
        max_pixels=36,
    )

    assert result == {
        "file": "ramp.png",
        "colors": ramp,
        "size": [12, 3],
    }
    with Image.open(tmp_path / "ramp.png") as exported:
        assert exported.size == (12, 3)
        assert exported.mode == "RGB"

    sentinel = b"do not replace"
    (tmp_path / "occupied.png").write_bytes(sentinel)
    with pytest.raises(studio.StudioError, match=r"^output already exists$"):
        studio.export_ramp_png(
            "#5b6ee1",
            "occupied.png",
            trusted_root=tmp_path,
            stops=4,
            swatch_size=3,
        )
    assert (tmp_path / "occupied.png").read_bytes() == sentinel


def test_dither_preview_and_export_are_bounded_and_create_only(tmp_path):
    preview = studio.preview_dither(
        "#2196f3",
        "#ffeb3b",
        size=8,
        order=2,
        max_pixels=64,
    )

    assert isinstance(preview["image"], Image.Image)
    assert preview["image"].size == (8, 8)
    assert preview["image"].mode == "RGB"
    assert preview["colors"] == ["#2196f3", "#ffeb3b"]
    assert preview["order"] == 2

    exported = studio.export_dither_png(
        "#2196f3",
        "#ffeb3b",
        "dither.png",
        trusted_root=tmp_path,
        size=8,
        order=4,
        max_pixels=64,
    )
    assert exported == {
        "file": "dither.png",
        "colors": ["#2196f3", "#ffeb3b"],
        "order": 4,
        "size": [8, 8],
    }
    with Image.open(tmp_path / "dither.png") as image:
        image.verify()

    sentinel = b"existing dither"
    (tmp_path / "occupied-dither.png").write_bytes(sentinel)
    with pytest.raises(studio.StudioError, match=r"^output already exists$"):
        studio.export_dither_png(
            "#2196f3",
            "#ffeb3b",
            "occupied-dither.png",
            trusted_root=tmp_path,
        )
    assert (tmp_path / "occupied-dither.png").read_bytes() == sentinel


def test_cycle_requires_two_explicit_frames(tmp_path):
    _save_png(tmp_path / "frame-0.png")

    with pytest.raises(
        studio.StudioError,
        match=r"^choose at least two animation frames$",
    ):
        studio.analyze_cycle(["frame-0.png"], trusted_root=tmp_path)

    with pytest.raises(
        studio.StudioError,
        match=r"^choose animation frames explicitly$",
    ):
        studio.analyze_cycle("frame-*.png", trusted_root=tmp_path)


def test_cycle_forwards_fixed_work_ceilings(tmp_path, monkeypatch):
    frames = [
        _save_png(tmp_path / "frame-0.png"),
        _save_png(tmp_path / "frame-1.png", color=(215, 123, 65, 255)),
    ]
    calls = []

    def qa(paths, **kwargs):
        calls.append((paths, kwargs))
        return {
            "verdict": "PASS",
            "frame_names": ["private-a.png", "private-b.png"],
            "issues": [],
            "hints": [],
        }

    monkeypatch.setattr(studio, "qa_cycle", qa)

    report = studio.analyze_cycle(
        [path.name for path in frames],
        trusted_root=tmp_path,
    )

    assert len(calls) == 1
    paths, kwargs = calls[0]
    assert [Path(path).resolve() for path in paths] == [path.resolve() for path in frames]
    assert kwargs["max_frames"] == 32
    assert kwargs["max_frame_pixels"] == 1_048_576
    assert kwargs["max_total_pixels"] == 8_388_608
    assert kwargs["max_pair_pixels"] == 67_108_864
    assert report["frame_names"] == ["frame-0.png", "frame-1.png"]
    assert str(tmp_path) not in _public_text(report)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda root: studio.inspect_sprite("../outside.png", trusted_root=root),
            id="inspect-input",
        ),
        pytest.param(
            lambda root: studio.preview_quantization(
                "../outside.png",
                "lenk-fern-4",
                trusted_root=root,
            ),
            id="preview-input",
        ),
        pytest.param(
            lambda root: studio.export_quantized_png(
                "inside.png",
                "lenk-fern-4",
                "../outside-output.png",
                trusted_root=root,
            ),
            id="quantized-output",
        ),
        pytest.param(
            lambda root: studio.preview_quantization(
                "inside.png",
                None,
                trusted_root=root,
                palette_file="../outside-palette.json",
            ),
            id="palette-input",
        ),
        pytest.param(
            lambda root: studio.export_ramp_png(
                "#5b6ee1",
                "../outside-ramp.png",
                trusted_root=root,
            ),
            id="ramp-output",
        ),
        pytest.param(
            lambda root: studio.export_dither_png(
                "#000000",
                "#ffffff",
                "../outside-dither.png",
                trusted_root=root,
            ),
            id="dither-output",
        ),
        pytest.param(
            lambda root: studio.analyze_cycle(
                ["inside.png", "../outside.png"],
                trusted_root=root,
            ),
            id="cycle-input",
        ),
        pytest.param(
            lambda root: studio.export_aseprite(
                "../outside.aseprite",
                "aseprite-output",
                trusted_root=root,
                executable=root / "aseprite.exe",
            ),
            id="aseprite-input",
        ),
        pytest.param(
            lambda root: studio.export_aseprite(
                "inside.aseprite",
                "../outside-aseprite-output",
                trusted_root=root,
                executable=root / "aseprite.exe",
            ),
            id="aseprite-output",
        ),
    ],
)
def test_file_operations_reject_paths_outside_the_trusted_workspace(
    tmp_path, operation
):
    trusted = tmp_path / "workspace"
    trusted.mkdir()
    _save_png(trusted / "inside.png")
    (trusted / "inside.aseprite").write_bytes(b"synthetic document")
    _save_png(tmp_path / "outside.png")
    (tmp_path / "outside-palette.json").write_text(
        json.dumps({
            "name": "Outside",
            "author": "Private",
            "colors": ["000000", "ffffff"],
        }),
        encoding="utf-8",
    )
    (tmp_path / "outside.aseprite").write_bytes(b"private document")

    with pytest.raises(
        studio.StudioError,
        match=r"^file is outside the trusted workspace$",
    ) as raised:
        operation(trusted)

    assert str(tmp_path) not in str(raised.value)
    assert not (tmp_path / "outside-output.png").exists()
    assert not (tmp_path / "outside-ramp.png").exists()
    assert not (tmp_path / "outside-dither.png").exists()
    assert not (tmp_path / "outside-aseprite-output").exists()


def test_unexpected_failures_are_wrapped_in_a_fixed_path_free_error(
    tmp_path, monkeypatch
):
    _save_png(tmp_path / "source.png")
    private_path = tmp_path / "private" / "calibration.png"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"decoder failed at {private_path}")

    monkeypatch.setattr(studio, "critique", fail)

    with pytest.raises(studio.StudioError) as raised:
        studio.inspect_sprite("source.png", trusted_root=tmp_path)

    assert str(raised.value) == "sprite inspection failed"
    assert str(tmp_path) not in str(raised.value)
    assert str(private_path) not in str(raised.value)


def test_aseprite_adapters_forward_the_explicit_security_context(
    tmp_path, monkeypatch
):
    document = tmp_path / "hero.aseprite"
    document.write_bytes(b"synthetic document")
    executable = tmp_path.parent / "programs" / "aseprite.exe"
    calls = []

    def export(document_arg, out_dir, **kwargs):
        calls.append(("export", document_arg, out_dir, kwargs))
        return {"kind": "aseprite-export", "frame_count": 3}

    def qa(document_arg, **kwargs):
        calls.append(("qa", document_arg, None, kwargs))
        return {"verdict": "PASS", "frame_names": ["frame-0", "frame-1"]}

    monkeypatch.setattr(studio, "export_aseprite_document", export)
    monkeypatch.setattr(studio, "qa_aseprite_document", qa)

    exported = studio.export_aseprite(
        document.name,
        "exports/hero",
        trusted_root=tmp_path,
        executable=executable,
        tag="walk",
        layer="body",
    )
    checked = studio.qa_aseprite(
        document.name,
        trusted_root=tmp_path,
        executable=executable,
        tag="walk",
        layer="body",
    )

    assert exported["frame_count"] == 3
    assert checked["verdict"] == "PASS"
    assert [kind for kind, _document, _output, _kwargs in calls] == ["export", "qa"]
    for _kind, forwarded_document, _output, kwargs in calls:
        assert Path(forwarded_document).resolve() == document.resolve()
        assert Path(kwargs["trusted_root"]).resolve() == tmp_path.resolve()
        assert Path(kwargs["executable"]) == executable
        assert kwargs["tag"] == "walk"
        assert kwargs["layer"] == "body"
    assert calls[0][2] == "exports/hero"
