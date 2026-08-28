"""MCP server contract and containment tests."""
from io import StringIO
import json
import sys

import pytest
from PIL import Image

from lenkraster import mcp_server


def _ready_session():
    session = mcp_server._Session()
    initialized = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
        session=session,
    )
    assert initialized["result"]["protocolVersion"] == "2025-11-25"
    assert mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        session=session,
    ) is None
    return session


def _request(name, args, trusted_root):
    return mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
        trusted_root=trusted_root,
        session=_ready_session(),
    )


def _call(name, args, trusted_root):
    resp = _request(name, args, trusted_root)
    assert "error" not in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


def _assert_tool_error(response, message):
    assert "error" not in response
    assert response["result"] == {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"error": message},
        "isError": True,
    }


def _save_png(path, size=(32, 32), color=(120, 60, 40, 255)):
    Image.new("RGBA", size, color).save(path)


def _public_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_public_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_public_text(item) for item in value)
    return ""


def test_initialize_and_list(tmp_path):
    session = mcp_server._Session()
    resp = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
        trusted_root=tmp_path,
        session=session,
    )
    assert resp["result"]["serverInfo"]["name"] == "lenkraster"
    assert mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        session=session,
    ) is None
    listing = mcp_server._handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        trusted_root=tmp_path,
        session=session,
    )
    names = {tool["name"] for tool in listing["result"]["tools"]}
    assert {
        "aseprite_export",
        "critique_sprite",
        "make_ramp",
        "palette_quantize",
        "contrast_report",
        "qa_aseprite_cycle",
        "qa_cycle",
    } <= names
    assert len(names) == 7


def test_initialize_negotiates_current_protocol_and_requires_initialized_notification(
        monkeypatch, tmp_path):
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ]
    stdin = StringIO("".join(json.dumps(item) + "\n" for item in requests))
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setenv("LENKRASTER_TRUSTED_ROOT", str(tmp_path))

    mcp_server.main()

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    assert responses[1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32002, "message": "server is not initialized"},
    }
    assert len(responses[2]["result"]["tools"]) == 7


def test_initialize_rejects_missing_required_parameters(tmp_path):
    response = mcp_server._handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        trusted_root=tmp_path,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "invalid initialize parameters"},
    }


def test_modern_discovery_and_stateless_tool_listing(tmp_path):
    discovery = mcp_server._handle(
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
        trusted_root=tmp_path,
    )
    result = discovery["result"]
    assert result["resultType"] == "complete"
    assert "2026-07-28" in result["supportedVersions"]
    assert result["capabilities"] == {"tools": {}}
    assert result["ttlMs"] == 300_000
    assert result["cacheScope"] == "public"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "lenkraster"

    listing = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "modern-test",
                        "version": "1.0",
                    },
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        trusted_root=tmp_path,
    )
    assert listing["result"]["resultType"] == "complete"
    assert len(listing["result"]["tools"]) == 7


def test_modern_request_rejects_unsupported_protocol_with_supported_versions(tmp_path):
    response = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2099-01-01",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        trusted_root=tmp_path,
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {
            "code": -32022,
            "message": "unsupported protocol version",
            "data": {
                "requested": "2099-01-01",
                "supported": list(mcp_server.SUPPORTED_PROTOCOLS),
            },
        },
    }


def test_notifications_never_execute_tools_or_receive_responses(tmp_path, monkeypatch):
    calls = []

    def forbidden(_arguments, _root):
        calls.append(True)
        raise AssertionError("tool notification must not execute")

    monkeypatch.setitem(mcp_server.TOOLS["make_ramp"], "fn", forbidden)
    response = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "make_ramp",
                "arguments": {"color": "#5b6ee1"},
            },
        },
        trusted_root=tmp_path,
    )

    assert response is None
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1, "method": "ping"},
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1, "method": 7},
    ],
)
def test_invalid_jsonrpc_envelopes_are_rejected(payload, tmp_path):
    assert mcp_server._handle(payload, trusted_root=tmp_path) == {
        "jsonrpc": "2.0",
        "id": payload.get("id") if isinstance(payload.get("id"), (int, str)) else None,
        "error": {"code": -32600, "message": "invalid request"},
    }


def test_inbound_jsonrpc_responses_are_ignored(tmp_path):
    assert mcp_server._handle(
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        trusted_root=tmp_path,
    ) is None


def test_tool_failures_use_mcp_tool_execution_errors(tmp_path):
    response = _request("make_ramp", {"color": "#zzzzzz"}, tmp_path)

    _assert_tool_error(response, "invalid color")


def test_tool_schemas_and_handlers_reject_unknown_arguments(tmp_path):
    listing = mcp_server._handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        trusted_root=tmp_path,
        session=_ready_session(),
    )
    assert all(
        tool["inputSchema"].get("additionalProperties") is False
        for tool in listing["result"]["tools"]
    )
    quantize_schema = next(
        tool["inputSchema"]
        for tool in listing["result"]["tools"]
        if tool["name"] == "palette_quantize"
    )
    assert quantize_schema["properties"]["palette"]["enum"] == [
        "lenk-cinder-16",
        "lenk-fern-4",
        "lenk-signal-16",
        "lenk-studio-32",
    ]
    assert quantize_schema["oneOf"] == [
        {"required": ["palette"]},
        {"required": ["palette_file"]},
    ]

    response = _request(
        "make_ramp",
        {"color": "#5b6ee1", "ignored": "must-not-be-accepted"},
        tmp_path,
    )
    _assert_tool_error(response, "invalid tool arguments")


def test_stdio_rejects_oversized_request_before_json_decode_and_recovers(monkeypatch):
    oversized = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",
            "padding": "x" * (64 * 1024),
        }
    ) + "\n"
    valid = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"
    original_loads = mcp_server.json.loads

    def guarded_loads(raw, **kwargs):
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise AssertionError("oversized request reached json.loads")
        return original_loads(raw, **kwargs)

    stdin = StringIO(oversized + valid)
    stdout = StringIO()
    monkeypatch.setattr(mcp_server.json, "loads", guarded_loads)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    mcp_server.main()

    responses = [original_loads(line) for line in stdout.getvalue().splitlines()]
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "request exceeds safety limit"},
        },
        {"jsonrpc": "2.0", "id": 7, "result": {}},
    ]


def test_bounded_input_stops_after_an_unterminated_oversized_request():
    class EndlessRequest:
        def __init__(self):
            self.calls = 0

        def readline(self, limit):
            self.calls += 1
            if self.calls > 3:
                raise AssertionError("oversized request drain is unbounded")
            return b"x" * limit

    source = EndlessRequest()
    lines = mcp_server._bounded_input_lines(source)

    assert next(lines) is mcp_server._OVERSIZED_REQUEST
    with pytest.raises(StopIteration):
        next(lines)
    assert source.calls == 2


@pytest.mark.parametrize(
    "number",
    ["9" * 1024, "1e" + "9" * 1024, "NaN", "Infinity", "-Infinity"],
)
def test_stdio_rejects_oversized_numeric_tokens_and_recovers(
    monkeypatch, number
):
    hostile = (
        '{"jsonrpc":"2.0","id":7,"method":"ping","ignored":'
        + number
        + "}\n"
    )
    valid = json.dumps({"jsonrpc": "2.0", "id": 8, "method": "ping"}) + "\n"
    stdin = StringIO(hostile + valid)
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    mcp_server.main()

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "parse error"},
        },
        {"jsonrpc": "2.0", "id": 8, "result": {}},
    ]


@pytest.mark.parametrize(
    "request_id",
    [True, 1.5, {}, [], "x" * 129, "é" * 65, 2**53],
)
def test_invalid_request_ids_are_rejected_without_echo(request_id, tmp_path):
    response = mcp_server._handle(
        {"jsonrpc": "2.0", "id": request_id, "method": "ping"},
        trusted_root=tmp_path,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "invalid request"},
    }


def test_make_ramp_tool(tmp_path):
    out = _call("make_ramp", {"color": "#5b6ee1", "stops": 4}, tmp_path)
    assert len(out["ramp"]) == 4


def test_tool_calls_require_an_explicit_trusted_root_environment(monkeypatch):
    monkeypatch.delenv("LENKRASTER_TRUSTED_ROOT", raising=False)

    response = mcp_server._handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "make_ramp",
                "arguments": {"color": "#5b6ee1"},
            },
        },
        session=_ready_session(),
    )

    _assert_tool_error(response, "trusted root is unavailable")


def test_aseprite_export_tool_uses_trusted_root_and_returns_sanitized_manifest(
        tmp_path, monkeypatch):
    calls = []

    def export(document, out_dir, **kwargs):
        calls.append((document, out_dir, kwargs))
        return {
            "schema_version": 1,
            "kind": "aseprite-export",
            "sheet": "sheet.png",
            "frame_count": 2,
        }

    monkeypatch.setattr(mcp_server, "export_aseprite_document", export)

    result = _call(
        "aseprite_export",
        {
            "document": "sprite.aseprite",
            "out_dir": "exports/walk",
            "tag": "walk",
            "layer": "body",
        },
        tmp_path,
    )

    assert result["frame_count"] == 2
    assert calls == [(
        "sprite.aseprite",
        "exports/walk",
        {"trusted_root": tmp_path, "tag": "walk", "layer": "body"},
    )]
    assert str(tmp_path) not in _public_text(result)


def test_qa_aseprite_cycle_tool_bounds_thresholds(tmp_path, monkeypatch):
    calls = []

    def qa(document, **kwargs):
        calls.append((document, kwargs))
        return {"verdict": "PASS", "frames": 2, "issues": [], "hints": []}

    monkeypatch.setattr(mcp_server, "qa_aseprite_document", qa)

    result = _call(
        "qa_aseprite_cycle",
        {
            "document": "sprite.ase",
            "motion_threshold": 12,
            "min_motion_pixels": 3,
        },
        tmp_path,
    )

    assert result["verdict"] == "PASS"
    assert calls == [(
        "sprite.ase",
        {
            "trusted_root": tmp_path,
            "tag": None,
            "layer": None,
            "motion_threshold": 12.0,
            "min_motion_pixels": 3,
        },
    )]


def test_critique_and_contrast(tmp_path):
    image = tmp_path / "sprite.png"
    _save_png(image)

    report = _call("critique_sprite", {"image": str(image)}, tmp_path)
    assert 0 <= report["score"] <= 1
    contrast = _call("contrast_report", {"image": str(image)}, tmp_path)
    assert "summary" in contrast


def test_input_traversal_is_rejected_without_leaking_absolute_path(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "private.png"
    _save_png(outside)

    response = _request("critique_sprite", {"image": "../private.png"}, trusted)

    _assert_tool_error(response, "input image is outside trusted root")
    public_text = _public_text(response)
    assert str(tmp_path) not in public_text
    assert str(outside) not in public_text


def test_symlink_escape_is_rejected_without_leaking_target(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "private.png"
    _save_png(outside)
    link = trusted / "linked.png"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    response = _request("contrast_report", {"image": "linked.png"}, trusted)

    _assert_tool_error(response, "input image is outside trusted root")
    assert str(outside) not in _public_text(response)


def test_oversized_image_is_rejected_before_engine_call(tmp_path, monkeypatch):
    image = tmp_path / "sprite.png"
    _save_png(image, size=(3, 2))
    monkeypatch.setattr(mcp_server, "MAX_IMAGE_PIXELS", 4)

    response = _request("contrast_report", {"image": "sprite.png"}, tmp_path)

    _assert_tool_error(response, "image exceeds safety limits")


def test_cycle_frame_count_is_bounded(tmp_path, monkeypatch):
    for index in range(3):
        _save_png(tmp_path / f"frame-{index}.png", size=(4, 4))
    monkeypatch.setattr(mcp_server, "MAX_FRAMES", 2)

    response = _request("qa_cycle", {"frames": "frame-*.png"}, tmp_path)

    _assert_tool_error(response, "frame count exceeds safety limit")


def test_cycle_discovery_stops_after_bounded_directory_entries(tmp_path, monkeypatch):
    entry_limit = 3

    class FakeEntry:
        def __init__(self, name):
            self.name = name

    class SentinelScandir:
        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            self.calls += 1
            if self.calls > entry_limit + 1:
                raise AssertionError("directory discovery exceeded its entry budget")
            names = ("unrelated.txt", "nested-directory", "notes.json", "ignored.bin")
            return FakeEntry(names[self.calls - 1])

    entries = SentinelScandir()
    monkeypatch.setattr(mcp_server, "MAX_FRAME_DIRECTORY_ENTRIES", entry_limit, raising=False)
    monkeypatch.setattr(mcp_server.os, "scandir", lambda _path: entries)

    response = _request("qa_cycle", {"frames": "frame-*.png"}, tmp_path)

    _assert_tool_error(response, "frame discovery exceeds safety limit")
    assert entries.calls == entry_limit + 1


def test_cycle_combined_pixel_work_is_bounded(tmp_path, monkeypatch):
    _save_png(tmp_path / "frame-0.png", size=(4, 4))
    _save_png(tmp_path / "frame-1.png", size=(4, 4))
    monkeypatch.setattr(mcp_server, "MAX_CYCLE_PAIR_PIXELS", 63)

    response = _request("qa_cycle", {"frames": "frame-*.png"}, tmp_path)

    _assert_tool_error(response, "animation cycle exceeds safety limits")


def test_cycle_glob_cannot_traverse_trusted_root(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    _save_png(tmp_path / "frame-0.png", size=(4, 4))
    _save_png(tmp_path / "frame-1.png", size=(4, 4))

    response = _request("qa_cycle", {"frames": "../frame-*.png"}, trusted)

    _assert_tool_error(response, "frame pattern is outside trusted root")
    assert str(tmp_path) not in _public_text(response)


def test_cycle_rejects_non_png_frame_extensions(tmp_path):
    _save_png(tmp_path / "frame-0.png", size=(4, 4))
    Image.new("RGBA", (4, 4), (120, 60, 40, 255)).save(
        tmp_path / "frame-1.data", format="PNG"
    )

    response = _request("qa_cycle", {"frames": "frame-*"}, tmp_path)

    _assert_tool_error(response, "frames must be PNG files")


def test_cycle_report_does_not_serialize_absolute_frame_paths(tmp_path):
    _save_png(tmp_path / "frame-0.png", size=(4, 4), color=(0, 0, 0, 0))
    _save_png(tmp_path / "frame-1.png", size=(4, 4))

    report = _call("qa_cycle", {"frames": "frame-*.png"}, tmp_path)

    assert report["frame_names"] == ["frame-0.png", "frame-1.png"]
    assert str(tmp_path) not in _public_text(report)


def test_quantize_refuses_output_escape_and_existing_file(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    image = trusted / "sprite.png"
    _save_png(image, size=(4, 4))

    escape = _request(
        "palette_quantize",
        {"image": "sprite.png", "palette": "lenk-signal-16", "out": "../escaped.png"},
        trusted,
    )
    _assert_tool_error(escape, "output is outside trusted root")
    assert not (tmp_path / "escaped.png").exists()

    existing = trusted / "existing.png"
    sentinel = b"must not be overwritten"
    existing.write_bytes(sentinel)
    collision = _request(
        "palette_quantize",
        {"image": "sprite.png", "palette": "lenk-signal-16", "out": "existing.png"},
        trusted,
    )
    _assert_tool_error(collision, "output already exists")
    assert existing.read_bytes() == sentinel


def test_quantize_writes_a_new_relative_png(tmp_path):
    image = tmp_path / "sprite.png"
    _save_png(image, size=(4, 4))

    result = _call(
        "palette_quantize",
        {"image": "sprite.png", "palette": "lenk-signal-16"},
        tmp_path,
    )

    assert result["path"] == "sprite.lenk-signal-16.png"
    assert (tmp_path / result["path"]).is_file()


def test_quantize_accepts_bounded_user_palette_below_trusted_root(tmp_path):
    _save_png(tmp_path / "sprite.png", size=(4, 4))
    palettes = tmp_path / "palettes"
    palettes.mkdir()
    (palettes / "user.json").write_text(json.dumps({
        "name": "User-owned compatibility palette",
        "author": "Local user",
        "colors": ["000000", "ffffff"],
    }), encoding="utf-8")

    result = _call(
        "palette_quantize",
        {"image": "sprite.png", "palette_file": "palettes/user.json"},
        tmp_path,
    )

    assert result["path"] == "sprite.user-palette.png"
    assert (tmp_path / result["path"]).is_file()
    assert str(tmp_path) not in _public_text(result)


def test_quantize_rejects_palette_escape_or_ambiguous_source(tmp_path):
    _save_png(tmp_path / "sprite.png", size=(4, 4))
    outside = tmp_path.parent / "outside-palette.json"
    outside.write_text(json.dumps({
        "name": "Outside",
        "author": "Local user",
        "colors": ["000000", "ffffff"],
    }), encoding="utf-8")

    escaped = _request(
        "palette_quantize",
        {"image": "sprite.png", "palette_file": "../outside-palette.json"},
        tmp_path,
    )
    _assert_tool_error(escaped, "user palette is outside trusted root")
    assert str(tmp_path) not in _public_text(escaped)

    ambiguous = _request(
        "palette_quantize",
        {
            "image": "sprite.png",
            "palette": "lenk-fern-4",
            "palette_file": "user.json",
        },
        tmp_path,
    )
    _assert_tool_error(ambiguous, "choose one palette source")


def test_unexpected_tool_error_is_sanitized(tmp_path, monkeypatch):
    private_path = tmp_path / "private" / "source.png"

    def fail(_args, _trusted_root):
        raise RuntimeError(f"decoder failed at {private_path}")

    monkeypatch.setitem(mcp_server.TOOLS["make_ramp"], "fn", fail)

    response = _request("make_ramp", {"color": "#5b6ee1"}, tmp_path)

    _assert_tool_error(response, "tool execution failed")
    assert str(private_path) not in _public_text(response)
