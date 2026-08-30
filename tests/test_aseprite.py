"""Hardened optional Aseprite CLI bridge contracts."""

import json
import hashlib
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from lenkraster import aseprite


def _document(root, name="sprite.aseprite"):
    path = root / name
    path.write_bytes(b"synthetic-aseprite-document")
    return path


def _executable(root):
    path = root / "aseprite-test.exe"
    path.write_bytes(b"synthetic-executable")
    return path


def _metadata(width=8, height=8, durations=(100, 150)):
    frames = []
    for index, duration in enumerate(durations):
        frames.append({
            "filename": f"private-source {index}.aseprite",
            "frame": {"x": index * width, "y": 0, "w": width, "h": height},
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": width, "h": height},
            "sourceSize": {"w": width, "h": height},
            "duration": duration,
        })
    return {
        "frames": frames,
        "meta": {
            "app": "https://www.aseprite.org/",
            "version": "1.3.18.3-x64",
            "image": "private-staging-sheet.png",
            "format": "RGBA8888",
            "size": {"w": width * len(frames), "h": height},
            "scale": "1",
        },
    }


def _successful_runner(monkeypatch, metadata=None):
    calls = []
    metadata = metadata or _metadata()

    def run(argv, **kwargs):
        if argv[1:] == ["--version"]:
            kwargs["stdout"].write(b"Aseprite 1.3.18.3-x64\n")
            return subprocess.CompletedProcess(argv, 0)
        calls.append((argv, kwargs))
        sheet = Path(argv[argv.index("--sheet") + 1])
        data = Path(argv[argv.index("--data") + 1])
        width = metadata["meta"]["size"]["w"]
        height = metadata["meta"]["size"]["h"]
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        frame_width = metadata["frames"][0]["frame"]["w"]
        colors = ((220, 30, 40, 255), (30, 180, 80, 255), (40, 60, 220, 255))
        for index in range(len(metadata["frames"])):
            tile = Image.new("RGBA", (frame_width, height), colors[index % len(colors)])
            image.paste(tile, (index * frame_width, 0))
        image.save(sheet)
        data.write_text(json.dumps(metadata), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(aseprite.subprocess, "run", run)
    return calls


def test_export_uses_fixed_batch_argv_and_publishes_sanitized_create_only_files(
        tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    monkeypatch.setenv("TOP_SECRET", "must-not-reach-subprocess")
    monkeypatch.setenv("APPDATA", str(tmp_path / "private-appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "private-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "private-profile"))
    calls = _successful_runner(monkeypatch)

    manifest = aseprite.export_document(
        document.name,
        "exported",
        trusted_root=tmp_path,
        executable=executable,
        tag="walk",
        layer="body",
    )

    assert manifest == {
        "schema_version": 1,
        "kind": "aseprite-export",
        "sheet": "sheet.png",
        "frame_count": 2,
        "frame_size": [8, 8],
        "frames": [
            {"index": 0, "rect": [0, 0, 8, 8], "duration_ms": 100},
            {"index": 1, "rect": [8, 0, 8, 8], "duration_ms": 150},
        ],
        "selection": {"tag": "walk", "layer": "body"},
        "advisory": "Export only; LenkRaster does not approve artwork.",
    }
    published = tmp_path / "exported"
    assert (published / "sheet.png").is_file()
    assert json.loads((published / "manifest.json").read_text(encoding="utf-8")) == manifest
    public_text = json.dumps(manifest) + (published / "manifest.json").read_text()
    assert str(tmp_path) not in public_text
    assert "private-source" not in public_text
    assert "private-staging" not in public_text

    argv, kwargs = calls[0]
    assert argv[0] == str(executable.resolve())
    assert argv[1:3] == ["--batch", "--noinapp"]
    source = Path(argv[argv.index("--format") + 2]).resolve()
    assert source.name == "input.aseprite"
    assert source.is_relative_to(Path(kwargs["cwd"]).resolve())
    assert source != document.resolve()
    assert argv.index("--tag") < argv.index(str(source))
    assert argv.index("--layer") < argv.index(str(source))
    assert "--script" not in argv
    assert kwargs["shell"] is False
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["timeout"] == aseprite.ASEPRITE_TIMEOUT_SECONDS
    assert "TOP_SECRET" not in kwargs["env"]
    for name in ("APPDATA", "HOME", "USERPROFILE"):
        isolated = Path(kwargs["env"][name]).resolve()
        assert isolated.is_relative_to(Path(kwargs["cwd"]).resolve())
        assert "private-" not in str(isolated)


def test_executable_hash_pin_is_validated_before_process_start(tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    monkeypatch.setenv("LENKRASTER_ASEPRITE_SHA256", "0" * 64)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("untrusted executable must not start")

    monkeypatch.setattr(aseprite.subprocess, "run", forbidden)

    with pytest.raises(
            aseprite.AsepriteError,
            match="Aseprite executable verification failed",
    ):
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
        )

    monkeypatch.setenv(
        "LENKRASTER_ASEPRITE_SHA256",
        hashlib.sha256(executable.read_bytes()).hexdigest(),
    )
    calls = _successful_runner(monkeypatch)
    aseprite.qa_document(
        document.name,
        trusted_root=tmp_path,
        executable=executable,
    )
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["tag", "layer"])
def test_option_like_selection_is_rejected_before_process_start(
        tmp_path, monkeypatch, field):
    document = _document(tmp_path)
    executable = _executable(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("option-like selections must not reach Aseprite")

    monkeypatch.setattr(aseprite.subprocess, "run", forbidden)

    with pytest.raises(aseprite.AsepriteError, match="Aseprite selection is invalid"):
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
            **{field: "--script"},
        )


def test_export_rejects_unsupported_aseprite_metadata_version(tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    metadata = _metadata()
    metadata["meta"]["version"] = "1.2.40-x64"
    _successful_runner(monkeypatch, metadata)

    with pytest.raises(
            aseprite.AsepriteError,
            match="Aseprite version is unsupported",
    ):
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
        )


def test_publish_becomes_visible_only_after_complete_staging(tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    _successful_runner(monkeypatch)
    observed = []
    real_rename = aseprite._rename_directory_noreplace

    def checked_rename(source, destination):
        source = Path(source)
        destination = Path(destination)
        assert source.name.startswith(".lenkraster-publish-")
        assert (source / "sheet.png").is_file()
        assert (source / "manifest.json").is_file()
        assert not destination.exists()
        observed.append((source.name, destination.name))
        return real_rename(source, destination)

    monkeypatch.setattr(aseprite, "_rename_directory_noreplace", checked_rename)

    aseprite.export_document(
        document.name,
        "atomic-export",
        trusted_root=tmp_path,
        executable=executable,
    )

    assert len(observed) == 1
    assert observed[0][1] == "atomic-export"
    assert not any(
        path.name.startswith(".lenkraster-publish-") for path in tmp_path.iterdir()
    )


def test_publish_refuses_destination_created_during_atomic_publication(
        tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    _successful_runner(monkeypatch)
    real_rename = aseprite._rename_directory_noreplace

    def racing_rename(source, destination):
        Path(destination).mkdir()
        return real_rename(source, destination)

    monkeypatch.setattr(aseprite, "_rename_directory_noreplace", racing_rename)

    with pytest.raises(aseprite.AsepriteError, match="Aseprite output already exists"):
        aseprite.export_document(
            document.name,
            "raced-export",
            trusted_root=tmp_path,
            executable=executable,
        )

    raced = tmp_path / "raced-export"
    assert raced.is_dir()
    assert list(raced.iterdir()) == []
    assert not any(
        path.name.startswith(".lenkraster-publish-") for path in tmp_path.iterdir()
    )


def test_export_refuses_existing_output_without_running_aseprite(tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    (tmp_path / "exported").mkdir()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Aseprite must not run for an existing output")

    monkeypatch.setattr(aseprite.subprocess, "run", forbidden)

    with pytest.raises(aseprite.AsepriteError, match="Aseprite output already exists"):
        aseprite.export_document(
            document.name,
            "exported",
            trusted_root=tmp_path,
            executable=executable,
        )


@pytest.mark.parametrize("name", ["sprite.png", "sprite.gif", "sprite.txt"])
def test_document_must_use_an_aseprite_extension(tmp_path, name):
    document = _document(tmp_path, name)
    executable = _executable(tmp_path)

    with pytest.raises(
            aseprite.AsepriteError,
            match=r"Aseprite input must be an \.ase or \.aseprite file",
    ):
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
        )


def test_document_cannot_escape_trusted_root_and_error_is_path_free(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    _document(tmp_path)
    executable = _executable(tmp_path)

    with pytest.raises(aseprite.AsepriteError) as caught:
        aseprite.qa_document(
            "../sprite.aseprite",
            trusted_root=trusted,
            executable=executable,
        )

    assert str(tmp_path) not in str(caught.value)
    assert str(caught.value) == "Aseprite document is outside trusted root"


def test_executable_must_be_explicit_absolute_and_available(tmp_path, monkeypatch):
    document = _document(tmp_path)
    monkeypatch.delenv("LENKRASTER_ASEPRITE_EXECUTABLE", raising=False)

    with pytest.raises(
            aseprite.AsepriteError,
            match="Aseprite integration is unavailable",
    ):
        aseprite.qa_document(document.name, trusted_root=tmp_path)

    with pytest.raises(
            aseprite.AsepriteError,
            match="Aseprite integration is unavailable",
    ):
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable="aseprite",
        )


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_process_failures_are_fixed_and_path_free(tmp_path, monkeypatch, failure):
    document = _document(tmp_path)
    executable = _executable(tmp_path)

    def run(argv, **_kwargs):
        if argv[1:] == ["--version"]:
            _kwargs["stdout"].write(b"Aseprite 1.3.18.3-x64\n")
            return subprocess.CompletedProcess(argv, 0)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 1, output="private", stderr="private")
        return subprocess.CompletedProcess(argv, 9, stdout="private", stderr="private")

    monkeypatch.setattr(aseprite.subprocess, "run", run)

    with pytest.raises(aseprite.AsepriteError) as caught:
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
        )

    assert str(caught.value) == "Aseprite export failed"
    assert str(tmp_path) not in str(caught.value)


def test_duplicate_or_malformed_metadata_fails_closed(tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)

    def run(argv, **_kwargs):
        if argv[1:] == ["--version"]:
            _kwargs["stdout"].write(b"Aseprite 1.3.18.3-x64\n")
            return subprocess.CompletedProcess(argv, 0)
        sheet = Path(argv[argv.index("--sheet") + 1])
        data = Path(argv[argv.index("--data") + 1])
        Image.new("RGBA", (16, 8), (20, 30, 40, 255)).save(sheet)
        data.write_text('{"frames":[],"frames":[],"meta":{}}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(aseprite.subprocess, "run", run)

    with pytest.raises(aseprite.AsepriteError, match="Aseprite export failed"):
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
        )


def test_direct_cycle_qa_uses_validated_in_memory_frames_and_leaves_no_outputs(
        tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    _successful_runner(monkeypatch)

    report = aseprite.qa_document(
        document.name,
        trusted_root=tmp_path,
        executable=executable,
        motion_threshold=1,
        min_motion_pixels=1,
    )

    assert report["verdict"] == "PASS"
    assert report["frames"] == 2
    assert report["frame_names"] == ["aseprite-frame-0", "aseprite-frame-1"]
    assert report["aseprite_frame_durations_ms"] == [100, 150]
    assert report["advisory"] == "LenkRaster does not approve artwork."
    assert not any(path.name.startswith(".lenkraster-aseprite-") for path in tmp_path.iterdir())
    assert str(tmp_path) not in json.dumps(report)


def test_frame_and_total_work_limits_fail_before_cycle_analysis(tmp_path, monkeypatch):
    document = _document(tmp_path)
    executable = _executable(tmp_path)
    metadata = _metadata(width=9, height=8, durations=(100, 100))
    _successful_runner(monkeypatch, metadata)
    monkeypatch.setattr(aseprite, "MAX_FRAME_PIXELS", 64)

    with pytest.raises(
            aseprite.AsepriteError,
            match="Aseprite document exceeds safety limits",
    ):
        aseprite.qa_document(
            document.name,
            trusted_root=tmp_path,
            executable=executable,
        )
