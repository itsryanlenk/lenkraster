"""Opt-in compatibility check against an operator-installed Aseprite binary."""

import os
from pathlib import Path
import subprocess

import pytest

from lenkraster import aseprite


_FIXTURE_SCRIPT = r'''
local output = app.params["output"]
local sprite = Sprite(8, 8, ColorMode.RGB)
local original = sprite.layers[1]
local group = sprite:newGroup()
group.name = "characters"
local layer = sprite:newLayer()
layer.name = "héro body"
layer.parent = group

local colors = {
  Color { r = 255, g = 0, b = 0, a = 255 },
  Color { r = 0, g = 255, b = 0, a = 255 },
  Color { r = 0, g = 0, b = 255, a = 255 },
}
local durations = { 0.10, 0.15, 0.20 }

for index = 1, 3 do
  local frame
  if index == 1 then
    frame = sprite.frames[1]
  else
    frame = sprite:newEmptyFrame()
  end
  local image = Image(sprite.spec)
  image:clear(colors[index])
  sprite:newCel(layer, frame, image)
  frame.duration = durations[index]
end

sprite:deleteLayer(original)
local tag = sprite:newTag(sprite.frames[1], sprite.frames[3])
tag.name = "walk cycle"
sprite:saveAs(output)
sprite:close()
'''


def _fixture_environment(root):
    profile = root / "fixture-profile"
    temporary = root / "fixture-tmp"
    profile.mkdir()
    temporary.mkdir()
    environment = {
        name: os.environ[name]
        for name in ("PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "WINDIR")
        if name in os.environ
    }
    environment.update({
        "ASEPRITE_USER_FOLDER": str(profile / "aseprite"),
        "APPDATA": str(profile / "appdata"),
        "LOCALAPPDATA": str(profile / "localappdata"),
        "HOME": str(profile / "home"),
        "USERPROFILE": str(profile / "home"),
        "TEMP": str(temporary),
        "TMP": str(temporary),
    })
    return environment


def test_real_aseprite_export_supports_tag_nested_unicode_layer_and_qa(tmp_path):
    if os.environ.get("LENKRASTER_RUN_ASEPRITE_INTEGRATION") != "1":
        pytest.skip("set LENKRASTER_RUN_ASEPRITE_INTEGRATION=1 to run")
    executable_raw = os.environ.get("LENKRASTER_ASEPRITE_EXECUTABLE")
    if not executable_raw:
        pytest.skip("set LENKRASTER_ASEPRITE_EXECUTABLE to run")
    executable = Path(executable_raw).resolve(strict=True)
    script = tmp_path / "create-fixture.lua"
    document = tmp_path / "generated.aseprite"
    script.write_text(_FIXTURE_SCRIPT, encoding="utf-8")
    created = subprocess.run(  # nosec B603
        [
            str(executable),
            "--batch",
            "--noinapp",
            "--script-param",
            f"output={document}",
            "--script",
            str(script),
        ],
        cwd=tmp_path,
        env=_fixture_environment(tmp_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
        shell=False,
    )
    assert created.returncode == 0 and document.is_file(), "fixture creation failed"

    manifest = aseprite.export_document(
        document.name,
        "real-export",
        trusted_root=tmp_path,
        executable=executable,
        tag="walk cycle",
        layer="characters/héro body",
    )
    assert manifest["frame_count"] == 3
    assert manifest["selection"] == {
        "tag": "walk cycle",
        "layer": "characters/héro body",
    }
    assert (tmp_path / "real-export" / "sheet.png").is_file()

    report = aseprite.qa_document(
        document.name,
        trusted_root=tmp_path,
        executable=executable,
        tag="walk cycle",
        layer="characters/héro body",
        motion_threshold=1,
        min_motion_pixels=1,
    )
    assert report["frames"] == 3
    assert report["aseprite_frame_durations_ms"] == [100, 150, 200]
