"""Bounded bridge to a separately installed Aseprite command-line executable.

The bridge accepts only trusted local ``.ase``/``.aseprite`` documents, invokes
Aseprite with a fixed batch-export argument vector, validates every generated
byte before use, and publishes exports create-only.  Aseprite is an optional
operator dependency and is never bundled with LenkRaster.
"""

import ctypes
import errno
from io import BytesIO
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import shutil
# Required only for the constrained Aseprite CLI bridge below.
import subprocess  # nosec B404
import sys
import tempfile

import numpy as np
from PIL import Image, UnidentifiedImageError

from .cycle import qa_cycle


ASEPRITE_TIMEOUT_SECONDS = 30
ASEPRITE_VERSION_TIMEOUT_SECONDS = 5
MIN_ASEPRITE_VERSION = (1, 3, 17, 2)
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_PATH_CHARS = 1024
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024
MAX_SHEET_BYTES = 16 * 1024 * 1024
MAX_FRAME_SIDE = 2048
MAX_FRAME_PIXELS = 1024 * 1024
MAX_FRAMES = 32
MAX_TOTAL_PIXELS = 8 * 1024 * 1024
MAX_PAIR_PIXELS = 64 * 1024 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 4096
MAX_JSON_NUMBER_CHARS = 64
MAX_FRAME_DURATION_MS = 60_000

_VERSION_PATTERN = re.compile(
    r"(?:Aseprite\s+)?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?:-[A-Za-z0-9_.-]+)?\Z"
)
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_SYSTEM_ENVIRONMENT = (
    "LANG",
    "LC_ALL",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
)


class AsepriteError(ValueError):
    """A fixed, path-free rejection safe to return to local callers."""


class _DuplicateKey(ValueError):
    pass


def _fail(message):
    raise AsepriteError(message)


def _trusted_root(raw):
    try:
        root = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail("Aseprite trusted root is unavailable")
    if not root.is_dir():
        _fail("Aseprite trusted root is unavailable")
    return root


def _is_within(root, path):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_value(raw, invalid_message):
    try:
        value = os.fspath(raw)
    except TypeError:
        _fail(invalid_message)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_CHARS
        or "\x00" in value
    ):
        _fail(invalid_message)
    return value


def _document(raw, root):
    value = _path_value(raw, "Aseprite document is unavailable")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("Aseprite document is unavailable")
    if not _is_within(root, path):
        _fail("Aseprite document is outside trusted root")
    if not path.is_file():
        _fail("Aseprite document is unavailable")
    if path.suffix.lower() not in (".ase", ".aseprite"):
        _fail("Aseprite input must be an .ase or .aseprite file")
    try:
        size = path.stat().st_size
    except OSError:
        _fail("Aseprite document is unavailable")
    if size <= 0 or size > MAX_DOCUMENT_BYTES:
        _fail("Aseprite document exceeds safety limits")
    return path


def _output_directory(raw, root):
    value = _path_value(raw, "Aseprite output is unavailable")
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    if candidate.name in ("", ".", ".."):
        _fail("Aseprite output is unavailable")
    try:
        parent = candidate.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("Aseprite output is unavailable")
    if not parent.is_dir() or not _is_within(root, parent):
        _fail("Aseprite output is outside trusted root")
    output = parent / candidate.name
    if os.path.lexists(output):
        _fail("Aseprite output already exists")
    return output


def _executable(raw=None):
    if raw is None:
        raw = os.environ.get("LENKRASTER_ASEPRITE_EXECUTABLE")
    try:
        value = os.fspath(raw)
    except TypeError:
        _fail("Aseprite integration is unavailable")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_CHARS
        or "\x00" in value
    ):
        _fail("Aseprite integration is unavailable")
    candidate = Path(value)
    if not candidate.is_absolute():
        _fail("Aseprite integration is unavailable")
    try:
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("Aseprite integration is unavailable")
    if not path.is_file():
        _fail("Aseprite integration is unavailable")
    _verify_executable_hash(path)
    return path


def _hash_executable(path):
    try:
        size = path.stat().st_size
        if size <= 0 or size > MAX_EXECUTABLE_BYTES:
            _fail("Aseprite executable verification failed")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except AsepriteError:
        raise
    except OSError:
        _fail("Aseprite executable verification failed")
    return digest.hexdigest()


def _verify_executable_hash(path):
    expected = os.environ.get("LENKRASTER_ASEPRITE_SHA256")
    if expected is None:
        return
    if _SHA256_PATTERN.fullmatch(expected) is None:
        _fail("Aseprite executable verification failed")
    actual = _hash_executable(path)
    if not hmac.compare_digest(actual, expected.lower()):
        _fail("Aseprite executable verification failed")


def _selection(value, *, nested=False):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "\\" in value
    ):
        _fail("Aseprite selection is invalid")
    parts = value.split("/")
    if (not nested and len(parts) != 1) or any(part in ("", ".", "..") for part in parts):
        _fail("Aseprite selection is invalid")
    return value


def _subprocess_environment(staging):
    profile = staging / "profile"
    temporary = staging / "tmp"
    locations = {
        "ASEPRITE_USER_FOLDER": profile / "aseprite",
        "HOME": profile / "home",
        "USERPROFILE": profile / "home",
        "APPDATA": profile / "appdata",
        "LOCALAPPDATA": profile / "localappdata",
        "XDG_CONFIG_HOME": profile / "xdg-config",
        "XDG_CACHE_HOME": profile / "xdg-cache",
        "XDG_DATA_HOME": profile / "xdg-data",
        "TEMP": temporary,
        "TMP": temporary,
        "TMPDIR": temporary,
    }
    try:
        for path in set(locations.values()):
            path.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail("Aseprite export failed")
    environment = {
        name: os.environ[name]
        for name in _SYSTEM_ENVIRONMENT
        if name in os.environ
    }
    environment.update({name: str(path) for name, path in locations.items()})
    return environment


def _parse_version(raw, *, minimum):
    if not isinstance(raw, str):
        _fail("Aseprite version is unsupported")
    match = _VERSION_PATTERN.fullmatch(raw.strip())
    if match is None:
        _fail("Aseprite version is unsupported")
    version = tuple(int(part or 0) for part in match.groups())
    if version[:2] != (1, 3) or version < minimum:
        _fail("Aseprite version is unsupported")
    return version


def _probe_version(executable, staging, environment):
    version_output = staging / "version.txt"
    argv = [str(executable), "--version"]
    try:
        with version_output.open("xb") as handle:
            completed = subprocess.run(  # nosec B603
                argv,
                cwd=str(staging),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.DEVNULL,
                timeout=ASEPRITE_VERSION_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        if completed.returncode != 0:
            _fail("Aseprite version is unsupported")
        raw = _read_bounded(version_output, 256)
        version = raw.decode("utf-8", errors="strict")
    except AsepriteError:
        raise
    except (OSError, UnicodeDecodeError, subprocess.TimeoutExpired):
        _fail("Aseprite version is unsupported")
    return _parse_version(version, minimum=MIN_ASEPRITE_VERSION)


def _snapshot_document(document, staging):
    snapshot = staging / "input.aseprite"
    try:
        with document.open("rb") as source:
            raw = source.read(MAX_DOCUMENT_BYTES + 1)
        if not raw or len(raw) > MAX_DOCUMENT_BYTES:
            _fail("Aseprite document exceeds safety limits")
        with snapshot.open("xb") as destination:
            destination.write(raw)
    except AsepriteError:
        raise
    except OSError:
        _fail("Aseprite document is unavailable")
    return snapshot


def _run_export(executable, document, staging, tag, layer, environment):
    sheet = staging / "sheet.png"
    metadata = staging / "sheet.json"
    argv = [str(executable), "--batch", "--noinapp"]
    if tag is not None:
        argv.extend(("--tag", tag))
    if layer is not None:
        argv.extend(("--layer", layer))
    argv.extend((
        "--sheet-type",
        "horizontal",
        "--format",
        "json-array",
        str(document),
        "--sheet",
        str(sheet),
        "--data",
        str(metadata),
    ))
    try:
        # The executable is an existing absolute file, argv is fixed and bounded,
        # and shell execution is explicitly disabled.
        completed = subprocess.run(  # nosec B603
            argv,
            cwd=str(staging),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=ASEPRITE_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("Aseprite export failed")
    if completed.returncode != 0:
        _fail("Aseprite export failed")
    if not sheet.is_file() or not metadata.is_file():
        _fail("Aseprite export failed")
    return sheet, metadata


def _read_bounded(path, limit):
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        _fail("Aseprite export failed")
    if not raw or len(raw) > limit:
        _fail("Aseprite export failed")
    return raw


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey()
        result[key] = value
    return result


def _parse_int(raw):
    if len(raw) > MAX_JSON_NUMBER_CHARS:
        raise ValueError()
    return int(raw, 10)


def _parse_float(raw):
    if len(raw) > MAX_JSON_NUMBER_CHARS:
        raise ValueError()
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError()
    return value


def _reject_constant(_raw):
    raise ValueError()


def _bounded_json_tree(value):
    nodes = 0
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _fail("Aseprite export failed")
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                _fail("Aseprite export failed")
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif current is not None and not isinstance(current, (str, int, float, bool)):
            _fail("Aseprite export failed")
        elif isinstance(current, float) and not math.isfinite(current):
            _fail("Aseprite export failed")


def _mapping(value):
    if not isinstance(value, dict):
        _fail("Aseprite export failed")
    return value


def _integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail("Aseprite export failed")
    return value


def _rectangle(value, maximum_width, maximum_height):
    value = _mapping(value)
    if set(value) != {"x", "y", "w", "h"}:
        _fail("Aseprite export failed")
    x = _integer(value["x"], 0, maximum_width)
    y = _integer(value["y"], 0, maximum_height)
    width = _integer(value["w"], 1, maximum_width)
    height = _integer(value["h"], 1, maximum_height)
    if x + width > maximum_width or y + height > maximum_height:
        _fail("Aseprite export failed")
    return x, y, width, height


def _size(value):
    value = _mapping(value)
    if set(value) != {"w", "h"}:
        _fail("Aseprite export failed")
    width = _integer(value["w"], 1, MAX_FRAME_SIDE * MAX_FRAMES)
    height = _integer(value["h"], 1, MAX_FRAME_SIDE)
    return width, height


def _metadata(path, executable_version):
    raw = _read_bounded(path, MAX_METADATA_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, RecursionError, ValueError):
        _fail("Aseprite export failed")
    _bounded_json_tree(value)
    value = _mapping(value)
    if set(value) != {"frames", "meta"}:
        _fail("Aseprite export failed")
    frames = value["frames"]
    meta = _mapping(value["meta"])
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        _fail("Aseprite document exceeds safety limits")
    if "size" not in meta:
        _fail("Aseprite export failed")
    metadata_version = _parse_version(
        meta.get("version"),
        minimum=(1, 3, 17, 0),
    )
    if metadata_version[:3] != executable_version[:3]:
        _fail("Aseprite version is unsupported")
    sheet_width, sheet_height = _size(meta["size"])
    if sheet_width * sheet_height > MAX_TOTAL_PIXELS:
        _fail("Aseprite document exceeds safety limits")

    allowed_frame_keys = {
        "duration",
        "filename",
        "frame",
        "rotated",
        "sourceSize",
        "spriteSourceSize",
        "trimmed",
    }
    checked = []
    frame_size = None
    for index, raw_frame in enumerate(frames):
        frame = _mapping(raw_frame)
        if set(frame) != allowed_frame_keys:
            _fail("Aseprite export failed")
        if frame["rotated"] is not False or frame["trimmed"] is not False:
            _fail("Aseprite export failed")
        rect = _rectangle(frame["frame"], sheet_width, sheet_height)
        x, y, width, height = rect
        source_size = _size(frame["sourceSize"])
        source_rect = _rectangle(frame["spriteSourceSize"], width, height)
        if source_size != (width, height) or source_rect != (0, 0, width, height):
            _fail("Aseprite export failed")
        if width > MAX_FRAME_SIDE or height > MAX_FRAME_SIDE or width * height > MAX_FRAME_PIXELS:
            _fail("Aseprite document exceeds safety limits")
        if frame_size is None:
            frame_size = (width, height)
        if frame_size != (width, height):
            _fail("Aseprite export failed")
        if x != index * width or y != 0:
            _fail("Aseprite export failed")
        duration = _integer(frame["duration"], 1, MAX_FRAME_DURATION_MS)
        checked.append({
            "index": index,
            "rect": [x, y, width, height],
            "duration_ms": duration,
        })
    width, height = frame_size
    if (sheet_width, sheet_height) != (width * len(checked), height):
        _fail("Aseprite export failed")
    if width * height * len(checked) > MAX_TOTAL_PIXELS:
        _fail("Aseprite document exceeds safety limits")
    return (sheet_width, sheet_height), (width, height), checked


def _validate_sheet(path, expected_size):
    try:
        size = path.stat().st_size
    except OSError:
        _fail("Aseprite export failed")
    if size <= 0 or size > MAX_SHEET_BYTES:
        _fail("Aseprite export failed")
    raw = _read_bounded(path, MAX_SHEET_BYTES)
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG" or image.size != expected_size:
                _fail("Aseprite export failed")
            image.verify()
    except AsepriteError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        _fail("Aseprite export failed")
    return raw


def _manifest(frame_size, frames, tag, layer):
    selection = {}
    if tag is not None:
        selection["tag"] = tag
    if layer is not None:
        selection["layer"] = layer
    return {
        "schema_version": 1,
        "kind": "aseprite-export",
        "sheet": "sheet.png",
        "frame_count": len(frames),
        "frame_size": list(frame_size),
        "frames": frames,
        "selection": selection,
        "advisory": "Export only; LenkRaster does not approve artwork.",
    }


def _stage(document, trusted_root, executable, tag, layer, staging_parent):
    root = _trusted_root(trusted_root)
    source = _document(document, root)
    program = _executable(executable)
    tag = _selection(tag)
    layer = _selection(layer, nested=True)
    temporary = tempfile.TemporaryDirectory(
        prefix=".lenkraster-aseprite-",
        dir=staging_parent,
    )
    try:
        staging = Path(temporary.name)
        environment = _subprocess_environment(staging)
        executable_version = _probe_version(program, staging, environment)
        _verify_executable_hash(program)
        snapshot = _snapshot_document(source, staging)
        sheet, metadata = _run_export(
            program,
            snapshot,
            staging,
            tag,
            layer,
            environment,
        )
        sheet_size, frame_size, frames = _metadata(metadata, executable_version)
        sheet_raw = _validate_sheet(sheet, sheet_size)
        return temporary, sheet_raw, frame_size, frames, tag, layer
    except Exception:
        _safe_cleanup(temporary)
        raise


def _safe_cleanup(temporary):
    try:
        temporary.cleanup()
    except OSError:
        pass


def _raise_rename_error(error_code, destination):
    if error_code in (errno.EEXIST, errno.ENOTEMPTY):
        raise FileExistsError(
            error_code,
            os.strerror(error_code),
            os.fspath(destination),
        )
    raise OSError(error_code, os.strerror(error_code), os.fspath(destination))


def _rename_directory_noreplace(source, destination):
    """Atomically rename a directory while refusing an existing destination."""
    if os.name == "nt":
        # Windows directory renames already fail when the destination exists.
        os.rename(source, destination)
        return

    source_raw = os.fsencode(source)
    destination_raw = os.fsencode(destination)
    library = ctypes.CDLL(None, use_errno=True)

    if sys.platform.startswith("linux"):
        try:
            renameat2 = library.renameat2
        except AttributeError as error:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-replace rename is unavailable",
                os.fspath(destination),
            ) from error
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            source_raw,
            -100,
            destination_raw,
            1,
        )
    elif sys.platform == "darwin":
        try:
            renamex_np = library.renamex_np
        except AttributeError as error:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-replace rename is unavailable",
                os.fspath(destination),
            ) from error
        renamex_np.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_raw, destination_raw, 0x00000004)
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace rename is unavailable",
            os.fspath(destination),
        )

    if result != 0:
        _raise_rename_error(ctypes.get_errno(), destination)


def _publish(output, sheet_raw, manifest):
    manifest_raw = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    staging = None
    try:
        staging = Path(tempfile.mkdtemp(
            prefix=".lenkraster-publish-",
            dir=output.parent,
        ))
        for name, raw in (("sheet.png", sheet_raw), ("manifest.json", manifest_raw)):
            path = staging / name
            with path.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        if os.path.lexists(output):
            _fail("Aseprite output already exists")
        _rename_directory_noreplace(staging, output)
        staging = None
    except AsepriteError:
        raise
    except FileExistsError:
        _fail("Aseprite output already exists")
    except OSError:
        _fail("Aseprite output could not be written")
    finally:
        if staging is not None:
            try:
                shutil.rmtree(staging)
            except OSError:
                pass


def export_document(
        document,
        out_dir,
        *,
        trusted_root,
        executable=None,
        tag=None,
        layer=None):
    """Export a trusted Aseprite document to a create-only sheet and manifest."""
    root = _trusted_root(trusted_root)
    output = _output_directory(out_dir, root)
    temporary = None
    try:
        temporary, sheet_raw, frame_size, frames, tag, layer = _stage(
            document,
            root,
            executable,
            tag,
            layer,
            output.parent,
        )
        manifest = _manifest(frame_size, frames, tag, layer)
        _publish(output, sheet_raw, manifest)
        return manifest
    except AsepriteError:
        raise
    except Exception:
        _fail("Aseprite export failed")
    finally:
        if temporary is not None:
            _safe_cleanup(temporary)


def qa_document(
        document,
        *,
        trusted_root,
        executable=None,
        tag=None,
        layer=None,
        motion_threshold=15.0,
        min_motion_pixels=4):
    """Export a trusted Aseprite document transiently and run bounded cycle QA."""
    root = _trusted_root(trusted_root)
    temporary = None
    try:
        temporary, sheet_raw, frame_size, frames, _tag, _layer = _stage(
            document,
            root,
            executable,
            tag,
            layer,
            root,
        )
        if len(frames) < 2:
            _fail("Aseprite cycle requires at least two frames")
        width, height = frame_size
        pair_count = len(frames) * (len(frames) - 1) // 2
        if width * height * pair_count > MAX_PAIR_PIXELS:
            _fail("Aseprite document exceeds safety limits")
        try:
            with Image.open(BytesIO(sheet_raw)) as image:
                rgba = image.convert("RGBA")
                arrays = []
                for frame in frames:
                    x, y, frame_width, frame_height = frame["rect"]
                    arrays.append(np.array(
                        rgba.crop((x, y, x + frame_width, y + frame_height)),
                        dtype=np.uint8,
                    ))
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
            _fail("Aseprite export failed")
        report = qa_cycle(
            arrays,
            motion_threshold=motion_threshold,
            min_motion_pixels=min_motion_pixels,
            max_frames=MAX_FRAMES,
            max_frame_pixels=MAX_FRAME_PIXELS,
            max_total_pixels=MAX_TOTAL_PIXELS,
            max_pair_pixels=MAX_PAIR_PIXELS,
        )
        original_names = list(report["frame_names"])
        safe_names = [f"aseprite-frame-{index}" for index in range(len(frames))]
        report["frame_names"] = safe_names
        for key in ("issues", "hints"):
            sanitized = []
            for text in report.get(key, []):
                for original, safe in zip(original_names, safe_names):
                    text = text.replace(original, safe)
                sanitized.append(text)
            report[key] = sanitized
        report["aseprite_frame_durations_ms"] = [
            frame["duration_ms"] for frame in frames
        ]
        report["advisory"] = "LenkRaster does not approve artwork."
        return report
    except AsepriteError:
        raise
    except Exception:
        _fail("Aseprite export failed")
    finally:
        if temporary is not None:
            _safe_cleanup(temporary)


__all__ = [
    "AsepriteError",
    "ASEPRITE_TIMEOUT_SECONDS",
    "export_document",
    "qa_document",
]
