"""Adversarial security contracts for the LenkRaster Studio facade."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from lenkraster import aseprite, studio


def _save_png(path: Path, *, size=(4, 4), color=(91, 110, 225, 255)) -> Path:
    Image.new("RGBA", size, color).save(path, format="PNG")
    return path


def _make_directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not permitted for this test user")


def _nested_strings(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _nested_strings(key)
            yield from _nested_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _nested_strings(child)
    elif isinstance(value, str):
        yield value


def test_input_and_output_symlinks_cannot_escape_the_trusted_workspace(tmp_path):
    trusted = tmp_path / "trusted"
    outside = tmp_path / "outside"
    trusted.mkdir()
    outside.mkdir()
    _save_png(outside / "private.png")

    try:
        (trusted / "linked.png").symlink_to(outside / "private.png")
    except OSError:
        pytest.skip("file symlinks are not permitted for this test user")

    with pytest.raises(
        studio.StudioError,
        match=r"^file is outside the trusted workspace$",
    ):
        studio.load_preview("linked.png", trusted_root=trusted)

    (trusted / "linked.png").unlink()
    _make_directory_symlink_or_skip(trusted / "exports", outside)
    with pytest.raises(
        studio.StudioError,
        match=r"^file is outside the trusted workspace$",
    ):
        studio.export_ramp_png(
            "#5b6ee1",
            "exports/ramp.png",
            trusted_root=trusted,
        )

    assert list(outside.iterdir()) == [outside / "private.png"]


def test_output_parent_swap_cannot_redirect_publication_outside_workspace(
    tmp_path, monkeypatch
):
    trusted = tmp_path / "trusted"
    exports = trusted / "exports"
    preserved_exports = trusted / "exports-before-swap"
    outside = tmp_path / "outside"
    exports.mkdir(parents=True)
    outside.mkdir()
    original_build_ramp = studio.build_ramp

    def swap_parent_then_build(*args, **kwargs):
        exports.rename(preserved_exports)
        try:
            exports.symlink_to(outside, target_is_directory=True)
        except OSError:
            preserved_exports.rename(exports)
            pytest.skip("directory symlinks are not permitted for this test user")
        return original_build_ramp(*args, **kwargs)

    monkeypatch.setattr(studio, "build_ramp", swap_parent_then_build)
    try:
        with pytest.raises(studio.StudioError):
            studio.export_ramp_png(
                "#5b6ee1",
                "exports/ramp.png",
                trusted_root=trusted,
                stops=4,
                swatch_size=3,
            )
        assert not (outside / "ramp.png").exists()
    finally:
        if exports.is_symlink():
            exports.unlink()
        if preserved_exports.exists() and not exports.exists():
            preserved_exports.rename(exports)


def test_png_extension_and_decoded_format_are_both_enforced(tmp_path):
    Image.new("RGB", (2, 2), (10, 20, 30)).save(
        tmp_path / "jpeg-disguised-as-png.png",
        format="JPEG",
    )
    _save_png(tmp_path / "png-disguised-as-jpeg.jpg")

    with pytest.raises(
        studio.StudioError,
        match=r"^input must be a PNG file$",
    ):
        studio.load_preview(
            "jpeg-disguised-as-png.png",
            trusted_root=tmp_path,
        )

    with pytest.raises(
        studio.StudioError,
        match=r"^file type is unsupported$",
    ):
        studio.load_preview(
            "png-disguised-as-jpeg.jpg",
            trusted_root=tmp_path,
        )


def test_encoded_and_decoded_png_limits_fail_before_use(tmp_path):
    encoded = tmp_path / "encoded-limit.png"
    with encoded.open("wb") as stream:
        stream.seek(studio.MAX_ENCODED_IMAGE_BYTES)
        stream.write(b"x")
    too_wide = _save_png(tmp_path / "too-wide.png", size=(2049, 1))

    for source in (encoded, too_wide):
        with pytest.raises(
            studio.StudioError,
            match=r"^image exceeds safety limits$",
        ):
            studio.load_preview(source.name, trusted_root=tmp_path)


def test_callers_cannot_raise_the_fixed_decoded_pixel_ceiling(tmp_path):
    source = _save_png(tmp_path / "over-ceiling.png", size=(1025, 1025))
    source_pixels = 1025 * 1025
    assert source_pixels > studio.MAX_IMAGE_PIXELS

    with pytest.raises(
        studio.StudioError,
        match=r"^image exceeds safety limits$",
    ):
        studio.load_preview(
            source.name,
            trusted_root=tmp_path,
            max_pixels=source_pixels,
        )


def test_callers_cannot_raise_the_fixed_ramp_work_ceiling(monkeypatch):
    swatch_size = 725
    pixels = (swatch_size * 2) * swatch_size
    assert pixels > studio.MAX_IMAGE_PIXELS

    def forbidden_allocation(*_args, **_kwargs):
        raise AssertionError("an over-ceiling image allocation was attempted")

    monkeypatch.setattr(studio.Image, "new", forbidden_allocation)
    with pytest.raises(
        studio.StudioError,
        match=r"^image exceeds safety limits$",
    ):
        studio.preview_ramp(
            "#5b6ee1",
            stops=2,
            swatch_size=swatch_size,
            max_pixels=pixels,
        )


def test_destination_created_after_validation_is_preserved(tmp_path, monkeypatch):
    destination = tmp_path / "raced.png"
    sentinel = b"competitor-owned content"
    original_validate = studio._validate_staged_png

    def validate_then_race(path, *, max_pixels):
        original_validate(path, max_pixels=max_pixels)
        destination.write_bytes(sentinel)

    monkeypatch.setattr(studio, "_validate_staged_png", validate_then_race)

    with pytest.raises(studio.StudioError, match=r"^output already exists$"):
        studio.export_ramp_png(
            "#5b6ee1",
            destination.name,
            trusted_root=tmp_path,
            stops=4,
            swatch_size=3,
        )

    assert destination.read_bytes() == sentinel
    assert not list(tmp_path.glob(".lenkraster-studio-*.png"))


def test_failed_publication_removes_partial_output_and_redacts_error(
    tmp_path, monkeypatch
):
    destination = tmp_path / "partial.png"
    private_path = tmp_path / "private" / "calibration.bin"

    def fail_after_partial_write(_reader, writer, **_kwargs):
        writer.write(b"partial")
        raise OSError(f"write failed at {private_path}")

    monkeypatch.setattr(studio.shutil, "copyfileobj", fail_after_partial_write)

    with pytest.raises(studio.StudioError) as caught:
        studio.export_ramp_png(
            "#5b6ee1",
            destination.name,
            trusted_root=tmp_path,
            stops=4,
            swatch_size=3,
        )

    assert str(caught.value) == "output could not be written"
    assert str(tmp_path) not in str(caught.value)
    assert not destination.exists()
    assert not list(tmp_path.glob(".lenkraster-studio-*.png"))


def test_cycle_report_recursively_removes_private_paths(tmp_path, monkeypatch):
    first = _save_png(tmp_path / "frame-0.png")
    second = _save_png(tmp_path / "frame-1.png", color=(220, 30, 40, 255))
    private_path = tmp_path / "private" / "calibration.png"

    def report_with_nested_path(_frames, **_kwargs):
        return {
            "verdict": "REVIEW",
            "frame_names": [str(first.resolve()), str(second.resolve())],
            "issues": [f"review {first.resolve()}"],
            "hints": [],
            "transition_group_reports": [
                {"name": "cycle", "debug_source": str(private_path)}
            ],
        }

    monkeypatch.setattr(studio, "qa_cycle", report_with_nested_path)
    report = studio.analyze_cycle(
        [first.name, second.name],
        trusted_root=tmp_path,
    )

    assert report["frame_names"] == [first.name, second.name]
    assert all(str(tmp_path) not in text for text in _nested_strings(report))


def test_cycle_postprocessing_exceptions_are_fixed_and_path_free(
    tmp_path, monkeypatch
):
    first = _save_png(tmp_path / "frame-0.png")
    second = _save_png(tmp_path / "frame-1.png", color=(220, 30, 40, 255))
    private_path = tmp_path / "private" / "cycle.json"

    class ExplodingFrameNames:
        def __iter__(self):
            raise RuntimeError(f"failed at {private_path}")

    monkeypatch.setattr(
        studio,
        "qa_cycle",
        lambda *_args, **_kwargs: {
            "verdict": "PASS",
            "frame_names": ExplodingFrameNames(),
            "issues": [],
            "hints": [],
        },
    )

    with pytest.raises(studio.StudioError) as caught:
        studio.analyze_cycle(
            [first.name, second.name],
            trusted_root=tmp_path,
        )

    assert str(caught.value) == "animation analysis failed"
    assert str(tmp_path) not in str(caught.value)


def test_explicit_aseprite_hash_cannot_override_a_conflicting_environment_pin(
    tmp_path, monkeypatch
):
    document = tmp_path / "hero.aseprite"
    document.write_bytes(b"synthetic-aseprite-document")
    executable = tmp_path / "aseprite.exe"
    executable.write_bytes(b"synthetic-executable")
    explicit_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    environment_digest = "0" * 64
    assert explicit_digest != environment_digest
    monkeypatch.setenv("LENKRASTER_ASEPRITE_SHA256", environment_digest)

    def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Aseprite started despite conflicting pins")

    monkeypatch.setattr(aseprite.subprocess, "run", forbidden_start)

    with pytest.raises(
        studio.StudioError,
        match=r"^Aseprite executable verification failed$",
    ):
        studio.qa_aseprite(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
            executable_sha256=explicit_digest,
        )


def test_aseprite_adapter_does_not_trust_exception_text(tmp_path, monkeypatch):
    document = tmp_path / "hero.aseprite"
    document.write_bytes(b"synthetic-aseprite-document")
    private_path = tmp_path / "private" / "aseprite.log"

    def fail_with_private_detail(*_args, **_kwargs):
        raise aseprite.AsepriteError(f"Aseprite failed at {private_path}")

    monkeypatch.setattr(studio, "qa_aseprite_document", fail_with_private_detail)

    with pytest.raises(studio.StudioError) as caught:
        studio.qa_aseprite(
            document.name,
            trusted_root=tmp_path,
            executable=tmp_path / "aseprite.exe",
        )

    assert str(caught.value) == "Aseprite analysis failed"
    assert str(tmp_path) not in str(caught.value)
