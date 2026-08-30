"""Bounded stdio MCP server for LenkRaster's local pixel-art engines.

File tools are confined to an explicit ``LENKRASTER_TRUSTED_ROOT``. Inputs must
be bounded PNGs, animation globs are relative and bounded, and generated output
may only be created below that root.
"""
import fnmatch
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from PIL import Image, UnidentifiedImageError

from .aseprite import (
    AsepriteError,
    export_document as export_aseprite_document,
    qa_document as qa_aseprite_document,
)
from .critic import critique
from .cycle import qa_cycle
from .palette import available_palettes, check_contrast, make_ramp, quantize_file


MODERN_PROTOCOL = "2026-07-28"
LEGACY_PROTOCOLS = ("2025-11-25", "2025-06-18", "2024-11-05")
SUPPORTED_PROTOCOLS = (MODERN_PROTOCOL,) + LEGACY_PROTOCOLS
PROTOCOL = LEGACY_PROTOCOLS[0]
PROTOCOL_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
SERVER_INFO = {"name": "lenkraster", "version": "0.1.1"}
DISCOVERY_TTL_MS = 300_000
MAX_PATH_CHARS = 1024
MAX_PATTERN_CHARS = 256
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_SIDE = 2048
MAX_IMAGE_PIXELS = 1024 * 1024
MIN_FRAMES = 2
MAX_FRAMES = 32
MAX_FRAME_DIRECTORY_ENTRIES = 4096
MAX_CYCLE_TOTAL_PIXELS = 8 * 1024 * 1024
MAX_CYCLE_PAIR_PIXELS = 64 * 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_REQUEST_ID_BYTES = 128
MAX_REQUEST_ID_INTEGER = 2**53 - 1
MAX_JSON_NUMBER_CHARS = 64

_OVERSIZED_REQUEST = object()
_INVALID_REQUEST_ENCODING = object()
_INVALID_REQUEST_ID = object()


class _RequestError(Exception):
    """An error whose fixed public message is safe to return to an MCP client."""

    def __init__(self, message, code=-32602):
        super().__init__(message)
        self.message = message
        self.code = code


class _Session:
    """Handshake-era lifecycle state for one stdio connection."""

    def __init__(self):
        self.phase = "new"
        self.protocol = None


def _error(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def _complete(result, modern=False, *, cacheable=False):
    body = dict(result)
    if modern:
        body["resultType"] = "complete"
        body["_meta"] = {SERVER_INFO_META_KEY: dict(SERVER_INFO)}
        if cacheable:
            body["ttlMs"] = DISCOVERY_TTL_MS
            body["cacheScope"] = "public"
    return body


def _tool_execution_error(request_id, message, modern=False):
    result = {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"error": message},
        "isError": True,
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": _complete(result, modern),
    }


def _implementation(value):
    if not isinstance(value, dict):
        return False
    name = value.get("name")
    version = value.get("version")
    return (
        isinstance(name, str)
        and 0 < len(name) <= 128
        and "\x00" not in name
        and isinstance(version, str)
        and 0 < len(version) <= 64
        and "\x00" not in version
    )


def _modern_request(request):
    params = request.get("params")
    if not isinstance(params, dict):
        return False, None
    meta = params.get("_meta")
    if not isinstance(meta, dict) or PROTOCOL_META_KEY not in meta:
        return False, None
    if meta.get(PROTOCOL_META_KEY) != MODERN_PROTOCOL:
        return True, ("unsupported protocol version", meta.get(PROTOCOL_META_KEY))
    client_info = meta.get(CLIENT_INFO_META_KEY)
    if client_info is not None and not _implementation(client_info):
        return True, ("invalid request metadata", None)
    capabilities = meta.get(CLIENT_CAPABILITIES_META_KEY, {})
    if not isinstance(capabilities, dict):
        return True, ("invalid request metadata", None)
    return True, None


def _initialize_protocol(params):
    if not isinstance(params, dict):
        raise _RequestError("invalid initialize parameters")
    requested = params.get("protocolVersion")
    capabilities = params.get("capabilities")
    client_info = params.get("clientInfo")
    if (
        not isinstance(requested, str)
        or len(requested) > 32
        or not isinstance(capabilities, dict)
        or not _implementation(client_info)
    ):
        raise _RequestError("invalid initialize parameters")
    if requested in LEGACY_PROTOCOLS:
        return requested
    return PROTOCOL


def _validated_request_id(request):
    request_id = request.get("id")
    if request_id is None:
        return None
    if isinstance(request_id, bool):
        return _INVALID_REQUEST_ID
    if isinstance(request_id, int):
        if -MAX_REQUEST_ID_INTEGER <= request_id <= MAX_REQUEST_ID_INTEGER:
            return request_id
        return _INVALID_REQUEST_ID
    if isinstance(request_id, str):
        try:
            encoded = request_id.encode("utf-8")
        except UnicodeEncodeError:
            return _INVALID_REQUEST_ID
        if len(encoded) <= MAX_REQUEST_ID_BYTES and "\x00" not in request_id:
            return request_id
    return _INVALID_REQUEST_ID


def _parse_json_int(raw):
    if len(raw) > MAX_JSON_NUMBER_CHARS:
        raise ValueError("JSON integer exceeds safety limit")
    return int(raw, 10)


def _parse_json_float(raw):
    if len(raw) > MAX_JSON_NUMBER_CHARS:
        raise ValueError("JSON float exceeds safety limit")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("JSON float is not finite")
    return value


def _reject_json_constant(_raw):
    raise ValueError("JSON constant is invalid")


def _bounded_input_lines(stream):
    source = getattr(stream, "buffer", stream)
    read_limit = MAX_REQUEST_BYTES + 3
    while True:
        line = source.readline(read_limit)
        if line in (b"", ""):
            return
        binary = isinstance(line, bytes)
        newline = b"\n" if binary else "\n"
        has_newline = line.endswith(newline)
        try:
            encoded = line if binary else line.encode("utf-8")
        except UnicodeEncodeError:
            yield _INVALID_REQUEST_ENCODING
            continue
        body = encoded[:-1] if encoded.endswith(b"\n") else encoded
        if body.endswith(b"\r"):
            body = body[:-1]
        if len(body) > MAX_REQUEST_BYTES:
            if not has_newline:
                remainder = source.readline(read_limit)
                if remainder not in (b"", ""):
                    try:
                        has_newline = remainder.endswith(newline)
                    except TypeError:
                        has_newline = False
            yield _OVERSIZED_REQUEST
            if not has_newline:
                return
            continue
        if binary:
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                yield _INVALID_REQUEST_ENCODING
                continue
        yield line


def _arguments(value, allowed):
    if not isinstance(value, dict) or not set(value).issubset(allowed):
        raise _RequestError("invalid tool arguments")
    return value


def _required_string(arguments, name, max_chars=MAX_PATH_CHARS):
    value = arguments.get(name)
    if not isinstance(value, str) or not value or len(value) > max_chars or "\x00" in value:
        raise _RequestError("invalid tool arguments")
    return value


def _optional_string(arguments, name, max_chars=64):
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_chars or "\x00" in value:
        raise _RequestError("invalid tool arguments")
    return value


def _trusted_root(override=None):
    raw = override
    if raw is None:
        raw = os.environ.get("LENKRASTER_TRUSTED_ROOT")
    if raw is None:
        raise _RequestError("trusted root is unavailable")
    try:
        root = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _RequestError("trusted root is unavailable") from None
    if not root.is_dir():
        raise _RequestError("trusted root is unavailable")
    return root


def _is_within(root, path):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _input_png(raw_path, root):
    try:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _RequestError("input image is unavailable") from None
    if not _is_within(root, path):
        raise _RequestError("input image is outside trusted root")
    if not path.is_file() or path.suffix.lower() != ".png":
        raise _RequestError("input must be a PNG file")
    _validate_png(path)
    return path


def _validate_png(path):
    try:
        file_size = path.stat().st_size
        if file_size <= 0 or file_size > MAX_IMAGE_BYTES:
            raise _RequestError("image exceeds safety limits")
        with Image.open(path) as image:
            if image.format != "PNG":
                raise _RequestError("input must be a PNG file")
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or width > MAX_IMAGE_SIDE
                or height > MAX_IMAGE_SIDE
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise _RequestError("image exceeds safety limits")
            image.verify()
    except _RequestError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        raise _RequestError("input is not a valid PNG") from None
    return width, height


def _output_png(raw_path, source, palette, root):
    if raw_path is None:
        candidate = source.with_name(f"{source.stem}.{palette}.png")
    else:
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or len(raw_path) > MAX_PATH_CHARS
            or "\x00" in raw_path
        ):
            raise _RequestError("invalid output path")
        requested = Path(raw_path).expanduser()
        candidate = requested if requested.is_absolute() else root / requested

    try:
        parent = candidate.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _RequestError("output directory is unavailable") from None
    if not _is_within(root, parent):
        raise _RequestError("output is outside trusted root")
    if not parent.is_dir() or candidate.name in ("", ".", ".."):
        raise _RequestError("invalid output path")
    output = parent / candidate.name
    if output.suffix.lower() != ".png":
        raise _RequestError("output must be a PNG file")
    if os.path.lexists(output):
        raise _RequestError("output already exists")
    return output


def _write_quantized(
        source,
        palette,
        output,
        *,
        palette_file=None,
        palette_root=None):
    temporary = None
    output_created = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".lenkraster-", suffix=".png", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        _, colors_used, used = quantize_file(
            str(source),
            palette,
            str(temporary),
            palette_file=palette_file,
            palette_root=palette_root,
        )
        _validate_png(temporary)

        try:
            with temporary.open("rb") as reader, output.open("xb") as writer:
                output_created = True
                shutil.copyfileobj(reader, writer, length=64 * 1024)
        except FileExistsError:
            raise _RequestError("output already exists") from None
        except OSError:
            if output_created:
                try:
                    output.unlink()
                except OSError:
                    pass
            raise _RequestError("output could not be written") from None
        return colors_used, used
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _frame_paths(pattern, root):
    if (
        not isinstance(pattern, str)
        or not pattern
        or len(pattern) > MAX_PATTERN_CHARS
        or "\x00" in pattern
    ):
        raise _RequestError("invalid frame pattern")

    requested = Path(pattern)
    parts = requested.parts
    if (
        requested.is_absolute()
        or requested.drive
        or ".." in parts
        or any("**" in part for part in parts)
        or any(any(mark in part for mark in "*?[") for part in parts[:-1])
    ):
        raise _RequestError("frame pattern is outside trusted root")
    if not parts or not requested.name:
        raise _RequestError("invalid frame pattern")

    try:
        parent = (root / requested.parent).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise _RequestError("frame directory is unavailable") from None
    if not _is_within(root, parent) or not parent.is_dir():
        raise _RequestError("frame directory is outside trusted root")

    paths = []
    seen = set()
    examined = 0
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                examined += 1
                if examined > MAX_FRAME_DIRECTORY_ENTRIES:
                    raise _RequestError("frame discovery exceeds safety limit")
                if not fnmatch.fnmatch(entry.name, requested.name):
                    continue
                candidate = parent / entry.name
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=True)
                if not _is_within(root, resolved):
                    raise _RequestError("frame is outside trusted root")
                if resolved.suffix.lower() != ".png":
                    raise _RequestError("frames must be PNG files")
                if resolved in seen:
                    continue
                seen.add(resolved)
                paths.append(resolved)
                if len(paths) > MAX_FRAMES:
                    raise _RequestError("frame count exceeds safety limit")
    except _RequestError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _RequestError("invalid frame pattern") from None

    paths.sort()
    if len(paths) < MIN_FRAMES:
        raise _RequestError("at least two frames are required")
    dimensions = {_validate_png(path) for path in paths}
    if len(dimensions) != 1:
        raise _RequestError("frames must share dimensions")
    width, height = next(iter(dimensions))
    frame_pixels = width * height
    if (
        frame_pixels * len(paths) > MAX_CYCLE_TOTAL_PIXELS
        or frame_pixels * len(paths) * len(paths) > MAX_CYCLE_PAIR_PIXELS
    ):
        raise _RequestError("animation cycle exceeds safety limits")
    return paths


def _bounded_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _RequestError("invalid tool arguments")
    return value


def _bounded_number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _RequestError("invalid tool arguments")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise _RequestError("invalid tool arguments")
    return number


def _tool_critique_sprite(arguments, root):
    arguments = _arguments(arguments, {"image"})
    image = _input_png(_required_string(arguments, "image"), root)
    report = critique(str(image))
    return {
        "score": report["score"],
        "findings": report["findings"],
        "retry_hints": report["retry_hints"],
    }


def _tool_make_ramp(arguments, _root):
    arguments = _arguments(arguments, {"color", "stops", "drift"})
    color = _required_string(arguments, "color", max_chars=7)
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", color) is None:
        raise _RequestError("invalid color")
    stops = _bounded_int(arguments.get("stops", 5), 1, 32)
    drift = _bounded_number(arguments.get("drift", -8.0), -360.0, 360.0)
    ramp = make_ramp(color, stops, drift)
    return {
        "ramp": ramp,
        "note": (
            "dark to light; negative drift walks shadows toward blue/purple "
            "(classic pixel-art hue shift)"
        ),
    }


def _tool_palette_quantize(arguments, root):
    arguments = _arguments(arguments, {"image", "palette", "palette_file", "out"})
    image = _input_png(_required_string(arguments, "image"), root)
    palette = _optional_string(arguments, "palette", max_chars=64)
    palette_file = _optional_string(
        arguments,
        "palette_file",
        max_chars=MAX_PATH_CHARS,
    )
    if (palette is None) == (palette_file is None):
        raise _RequestError("choose one palette source")
    if palette is not None and palette not in available_palettes():
        raise _RequestError("unknown palette")
    output_label = palette if palette is not None else "user-palette"
    output = _output_png(arguments.get("out"), image, output_label, root)
    try:
        colors_used, used = _write_quantized(
            image,
            palette,
            output,
            palette_file=palette_file,
            palette_root=root,
        )
    except (KeyError, ValueError) as error:
        raise _RequestError(str(error)) from None
    return {
        "path": output.relative_to(root).as_posix(),
        "colors_used": colors_used,
        "used": used,
        "note": "for animation frames, snap ALL frames against the same palette",
    }


def _tool_contrast_report(arguments, root):
    arguments = _arguments(arguments, {"image"})
    image = _input_png(_required_string(arguments, "image"), root)
    return check_contrast(str(image))


def _tool_qa_cycle(arguments, root):
    arguments = _arguments(arguments, {"frames"})
    paths = _frame_paths(arguments.get("frames"), root)
    report = qa_cycle([str(path) for path in paths])
    report["frame_names"] = [path.name for path in paths]
    for key in ("issues", "hints"):
        sanitized = []
        for text in report.get(key, []):
            for path in paths:
                text = text.replace(str(path), path.name)
            sanitized.append(text)
        report[key] = sanitized
    return report


def _tool_aseprite_export(arguments, root):
    arguments = _arguments(arguments, {"document", "out_dir", "tag", "layer"})
    document = _required_string(arguments, "document")
    out_dir = _required_string(arguments, "out_dir")
    tag = _optional_string(arguments, "tag")
    layer = _optional_string(arguments, "layer")
    try:
        return export_aseprite_document(
            document,
            out_dir,
            trusted_root=root,
            tag=tag,
            layer=layer,
        )
    except AsepriteError as error:
        raise _RequestError(str(error)) from None


def _tool_qa_aseprite_cycle(arguments, root):
    arguments = _arguments(
        arguments,
        {"document", "tag", "layer", "motion_threshold", "min_motion_pixels"},
    )
    document = _required_string(arguments, "document")
    tag = _optional_string(arguments, "tag")
    layer = _optional_string(arguments, "layer")
    motion_threshold = _bounded_number(
        arguments.get("motion_threshold", 15.0), 0.001, 1020.0
    )
    min_motion_pixels = _bounded_int(
        arguments.get("min_motion_pixels", 4), 1, MAX_IMAGE_PIXELS
    )
    try:
        return qa_aseprite_document(
            document,
            trusted_root=root,
            tag=tag,
            layer=layer,
            motion_threshold=motion_threshold,
            min_motion_pixels=min_motion_pixels,
        )
    except AsepriteError as error:
        raise _RequestError(str(error)) from None


def _annotations(read_only, idempotent):
    return {
        "readOnlyHint": read_only,
        "destructiveHint": False,
        "idempotentHint": idempotent,
        "openWorldHint": False,
    }


TOOLS = {
    "critique_sprite": {
        "description": (
            "Score one bounded PNG below the trusted root against pixel-art craft rules."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["image"],
            "properties": {"image": {"type": "string", "maxLength": MAX_PATH_CHARS}},
        },
        "annotations": _annotations(True, True),
        "fn": _tool_critique_sprite,
    },
    "make_ramp": {
        "description": "Generate a bounded hue-shifted OKLCH material ramp.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["color"],
            "properties": {
                "color": {"type": "string", "pattern": "^#?[0-9a-fA-F]{6}$"},
                "stops": {"type": "integer", "minimum": 1, "maximum": 32},
                "drift": {"type": "number", "minimum": -360, "maximum": 360},
            },
        },
        "annotations": _annotations(True, True),
        "fn": _tool_make_ramp,
    },
    "palette_quantize": {
        "description": (
            "Snap a bounded PNG to one built-in palette or a user-owned palette JSON "
            "below the trusted root, then create a new PNG. Existing files are never "
            "overwritten."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["image"],
            "oneOf": [
                {"required": ["palette"]},
                {"required": ["palette_file"]},
            ],
            "properties": {
                "image": {"type": "string", "maxLength": MAX_PATH_CHARS},
                "palette": {"type": "string", "enum": available_palettes()},
                "palette_file": {"type": "string", "maxLength": MAX_PATH_CHARS},
                "out": {"type": "string", "maxLength": MAX_PATH_CHARS},
            },
        },
        "annotations": _annotations(False, False),
        "fn": _tool_palette_quantize,
    },
    "contrast_report": {
        "description": "Report same-hue lightness separation for one bounded PNG.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["image"],
            "properties": {"image": {"type": "string", "maxLength": MAX_PATH_CHARS}},
        },
        "annotations": _annotations(True, True),
        "fn": _tool_contrast_report,
    },
    "qa_cycle": {
        "description": (
            "QA 2-32 same-size PNG frames matched by a relative filename glob below "
            "the trusted root."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["frames"],
            "properties": {
                "frames": {"type": "string", "maxLength": MAX_PATTERN_CHARS}
            },
        },
        "annotations": _annotations(True, True),
        "fn": _tool_qa_cycle,
    },
    "aseprite_export": {
        "description": (
            "Export one trusted local .ase/.aseprite document through a separately "
            "installed Aseprite CLI into a new sheet/manifest directory."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["document", "out_dir"],
            "properties": {
                "document": {"type": "string", "maxLength": MAX_PATH_CHARS},
                "out_dir": {"type": "string", "maxLength": MAX_PATH_CHARS},
                "tag": {"type": "string", "maxLength": 64},
                "layer": {"type": "string", "maxLength": 64},
            },
        },
        "annotations": _annotations(False, False),
        "fn": _tool_aseprite_export,
    },
    "qa_aseprite_cycle": {
        "description": (
            "Transiently export and QA a 2-32 frame trusted local Aseprite cycle. "
            "No export files are retained."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["document"],
            "properties": {
                "document": {"type": "string", "maxLength": MAX_PATH_CHARS},
                "tag": {"type": "string", "maxLength": 64},
                "layer": {"type": "string", "maxLength": 64},
                "motion_threshold": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1020,
                },
                "min_motion_pixels": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_IMAGE_PIXELS,
                },
            },
        },
        "annotations": _annotations(True, True),
        "fn": _tool_qa_aseprite_cycle,
    },
}


def _listed_tools():
    return [
        {
            "name": name,
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
            "annotations": tool["annotations"],
        }
        for name, tool in TOOLS.items()
    ]


def _handle(request, trusted_root=None, session=None):
    session = session or _Session()
    if not isinstance(request, dict):
        return _error(None, -32600, "invalid request")
    if (
        request.get("jsonrpc") == "2.0"
        and "method" not in request
        and "id" in request
        and ("result" in request or "error" in request)
    ):
        # LenkRaster never sends client-bound requests, so inbound responses are ignored.
        return None
    request_id = _validated_request_id(request)
    if (
        request.get("jsonrpc") != "2.0"
        or not isinstance(request.get("method"), str)
        or not request.get("method")
        or request_id is _INVALID_REQUEST_ID
        or ("id" in request and request.get("id") is None)
    ):
        safe_id = request_id if request_id is not _INVALID_REQUEST_ID else None
        return _error(safe_id, -32600, "invalid request")

    method = request["method"]
    is_notification = "id" not in request
    if is_notification:
        if method == "notifications/initialized" and session.phase == "awaiting_initialized":
            session.phase = "ready"
        # MCP operations are requests. Never execute a tool received without an id.
        return None

    modern, metadata_error = _modern_request(request)
    if metadata_error is not None:
        message, requested = metadata_error
        if message == "unsupported protocol version":
            return _error(
                request_id,
                -32022,
                message,
                {
                    "requested": requested,
                    "supported": list(SUPPORTED_PROTOCOLS),
                },
            )
        return _error(request_id, -32602, message)

    if method == "server/discover":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _complete(
                {
                    "supportedVersions": list(SUPPORTED_PROTOCOLS),
                    "capabilities": {"tools": {}},
                    "instructions": (
                        "LenkRaster performs bounded advisory pixel-art analysis and "
                        "create-only local transformations."
                    ),
                },
                True,
                cacheable=True,
            ),
        }

    if method == "initialize":
        if session.phase != "new":
            return _error(request_id, -32600, "invalid request")
        try:
            protocol = _initialize_protocol(request.get("params"))
        except _RequestError as error:
            return _error(request_id, error.code, error.message)
        session.protocol = protocol
        session.phase = "awaiting_initialized"
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": dict(SERVER_INFO),
                "instructions": (
                    "LenkRaster results are advisory and do not approve artwork."
                ),
            },
        }

    if not modern and method != "ping" and session.phase != "ready":
        return _error(request_id, -32002, "server is not initialized")

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _complete(
                {"tools": _listed_tools()}, modern, cacheable=modern
            ),
        }
    if method == "tools/call":
        params = request.get("params")
        allowed_params = {"name", "arguments", "_meta"}
        if modern:
            allowed_params.update({"inputResponses", "requestState"})
        if not isinstance(params, dict) or not set(params).issubset(allowed_params):
            return _error(request_id, -32602, "invalid tool arguments")
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOLS:
            return _error(request_id, -32602, "unknown tool")
        arguments = params.get("arguments", {})
        try:
            root = _trusted_root(trusted_root)
            result = TOOLS[name]["fn"](arguments, root)
            body = json.dumps(result, separators=(",", ":"))
        except _RequestError as error:
            return _tool_execution_error(request_id, error.message, modern)
        except Exception:
            return _tool_execution_error(request_id, "tool execution failed", modern)
        tool_result = {
            "content": [{"type": "text", "text": body}],
            "structuredContent": result,
        }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _complete(tool_result, modern),
        }
    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _complete({}, modern),
        }
    return _error(request_id, -32601, "method not found")


def main():
    session = _Session()
    for line in _bounded_input_lines(sys.stdin):
        if line is _OVERSIZED_REQUEST:
            response = _error(None, -32600, "request exceeds safety limit")
        elif line is _INVALID_REQUEST_ENCODING:
            response = _error(None, -32700, "parse error")
        elif not line.strip():
            continue
        else:
            try:
                request = json.loads(
                    line,
                    parse_int=_parse_json_int,
                    parse_float=_parse_json_float,
                    parse_constant=_reject_json_constant,
                )
            except (ValueError, RecursionError):
                response = _error(None, -32700, "parse error")
            else:
                response = _handle(request, session=session)
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
