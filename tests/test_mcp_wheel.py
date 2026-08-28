"""Installed-wheel regression for the lenkraster-mcp entry point."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_built_wheel_contains_and_runs_mcp_server(tmp_path):
    pytest.importorskip(
        "setuptools.build_meta",
        reason="offline wheel test requires the setuptools dev extra",
    )
    staging = tmp_path / "source"
    staging.mkdir()
    for name in (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "ASSET_LICENSE.md",
        "THIRD_PARTY_NOTICES.md",
    ):
        shutil.copy2(REPO_ROOT / name, staging / name)
    shutil.copytree(REPO_ROOT / "src", staging / "src")

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build_env = os.environ.copy()
    build_env.update({
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(staging),
        ],
        cwd=tmp_path,
        env=build_env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheels = list(wheel_dir.glob("lenkraster-*.whl"))
    assert len(wheels) == 1

    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        assert "lenkraster/mcp_server.py" in names
        assert any(name.endswith("/licenses/ASSET_LICENSE.md") for name in names)
        assert any(name.endswith("/licenses/THIRD_PARTY_NOTICES.md") for name in names)
        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode("utf-8")
        assert "lenkraster-mcp = lenkraster.mcp_main:main" in entry_points

    installed_wheel = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(installed),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        env=build_env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert installed_wheel.returncode == 0, installed_wheel.stdout + installed_wheel.stderr
    entrypoint_name = "lenkraster-mcp.exe" if os.name == "nt" else "lenkraster-mcp"
    entrypoint = installed / "bin" / entrypoint_name
    assert entrypoint.is_file()

    run_env = os.environ.copy()
    run_env.update({
        "LENKRASTER_TRUSTED_ROOT": str(tmp_path),
        "PYTHONPATH": str(installed),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    smoke = tmp_path / "smoke.png"
    Image.new("RGBA", (16, 16), (120, 60, 40, 255)).save(smoke)
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "lenkraster-wheel-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "critique_sprite",
                "arguments": {"image": "smoke.png"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "palette_quantize",
                "arguments": {
                    "image": "smoke.png",
                    "palette": "lenk-signal-16",
                    "out": "smoke.lenk-signal-16.png",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "palette_quantize",
                "arguments": {
                    "image": "smoke.png",
                    "palette": "lenk-signal-16",
                    "out": "smoke.lenk-signal-16.png",
                },
            },
        },
    ]
    request = "".join(json.dumps(item) + "\n" for item in requests)
    server = subprocess.run(
        [str(entrypoint)],
        cwd=tmp_path,
        env=run_env,
        input=request,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert server.returncode == 0, server.stderr
    responses = [json.loads(line) for line in server.stdout.splitlines()]
    assert len(responses) == 5
    assert responses[0]["result"]["serverInfo"] == {
        "name": "lenkraster",
        "version": "0.1.0",
    }
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert names == {
        "aseprite_export",
        "contrast_report",
        "critique_sprite",
        "make_ramp",
        "palette_quantize",
        "qa_aseprite_cycle",
        "qa_cycle",
    }
    critique = json.loads(responses[2]["result"]["content"][0]["text"])
    assert 0 <= critique["score"] <= 1
    quantized = json.loads(responses[3]["result"]["content"][0]["text"])
    assert quantized["path"] == "smoke.lenk-signal-16.png"
    output = tmp_path / quantized["path"]
    original = output.read_bytes()
    collision_response = responses[4]["result"]
    assert collision_response["isError"] is True
    assert collision_response["structuredContent"] == {
        "error": "output already exists"
    }
    assert output.read_bytes() == original
