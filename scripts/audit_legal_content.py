#!/usr/bin/env python3
"""Fail closed on retired identities, palette drift, and missing legal notices."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".in", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
MAX_BINARY_SCAN_BYTES = 8 * 1024 * 1024
FORBIDDEN_TERMS = (
    "pixel" + "forge",
    "endes" + "ga",
    "commodore" + "-64",
    "game" + "boy",
    "dawn" + "bringer",
    "pico" + "-8",
)
PALETTES = {
    "lenk-cinder-16.json": (
        "Lenk Cinder 16",
        16,
        "ce0fbb4c6e96b814356da83e23bb1fac536026a5bab198449008d1e37bf666d1",
    ),
    "lenk-fern-4.json": (
        "Lenk Fern 4",
        4,
        "9e3a08c13a530ce442085c6d724e57cd61f198db9bb456124fe2e25973a60683",
    ),
    "lenk-signal-16.json": (
        "Lenk Signal 16",
        16,
        "07eb4c2220e3cf72be559907ffc1dcc6d92401bf44b8716fe350e610018bd8fb",
    ),
    "lenk-studio-32.json": (
        "Lenk Studio 32",
        32,
        "d5570602f593d4d2645d142c81b06cb38892b85eb5873b36bde95915fc68f51a",
    ),
}
HEX_COLOR = re.compile(r"[0-9a-f]{6}\Z")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _tracked_paths() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [
        Path(item.decode("utf-8"))
        for item in completed.stdout.split(b"\0")
        if item
    ]


def _check_public_identity(findings: list[str]) -> None:
    for relative in _tracked_paths():
        lowered_name = relative.as_posix().lower()
        for term in FORBIDDEN_TERMS:
            if term in lowered_name:
                findings.append(f"retired identity in path: {relative.as_posix()}")
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            try:
                raw = path.read_bytes()
            except OSError:
                findings.append(f"binary unavailable: {relative.as_posix()}")
                continue
            if len(raw) > MAX_BINARY_SCAN_BYTES:
                findings.append(f"binary exceeds legal audit limit: {relative.as_posix()}")
                continue
            lowered_bytes = raw.lower()
            for term in FORBIDDEN_TERMS:
                if term.encode("ascii") in lowered_bytes:
                    findings.append(
                        f"retired identity in binary metadata: {relative.as_posix()}"
                    )
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeError):
            findings.append(f"text unavailable: {relative.as_posix()}")
            continue
        for term in FORBIDDEN_TERMS:
            if term in text:
                findings.append(f"retired identity in text: {relative.as_posix()}")


def _check_palettes(findings: list[str]) -> None:
    root = REPO_ROOT / "src" / "lenkraster" / "data" / "palettes"
    actual = {path.name for path in root.glob("*.json") if path.is_file()}
    if actual != set(PALETTES):
        findings.append("built-in palette inventory differs from the approved set")
        return
    for filename, (expected_name, expected_count, expected_hash) in PALETTES.items():
        path = root / filename
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            findings.append(f"invalid palette data: {filename}")
            continue
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            findings.append(f"palette provenance hash changed: {filename}")
        if not isinstance(data, dict) or set(data) != {"name", "author", "colors"}:
            findings.append(f"invalid palette schema: {filename}")
            continue
        colors = data["colors"]
        if data["name"] != expected_name or data["author"] != "Ryan Lenk":
            findings.append(f"palette authorship metadata changed: {filename}")
        if (
            not isinstance(colors, list)
            or len(colors) != expected_count
            or len(set(colors)) != expected_count
            or any(not isinstance(color, str) or not HEX_COLOR.fullmatch(color) for color in colors)
        ):
            findings.append(f"palette color inventory changed: {filename}")


def _check_notices(findings: list[str]) -> None:
    required = {
        "ASSET_LICENSE.md": ("MIT License", "Ryan Lenk", *PALETTES),
        "THIRD_PARTY_NOTICES.md": ("Aseprite", "NumPy", "Pillow", "OKLab"),
        "docs/name-clearance.md": ("LenkRaster", "not a legal opinion"),
    }
    for filename, phrases in required.items():
        path = REPO_ROOT / filename
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            findings.append(f"required legal file unavailable: {filename}")
            continue
        for phrase in phrases:
            if phrase not in text:
                findings.append(f"required legal phrase missing from {filename}: {phrase}")

    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    project = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for filename in ("ASSET_LICENSE.md", "THIRD_PARTY_NOTICES.md"):
        if f"include {filename}" not in manifest:
            findings.append(f"source distribution omits legal file: {filename}")
        if filename not in project:
            findings.append(f"wheel license metadata omits legal file: {filename}")


def audit() -> list[str]:
    findings: list[str] = []
    _check_public_identity(findings)
    _check_palettes(findings)
    _check_notices(findings)
    return sorted(set(findings))


def main() -> int:
    try:
        findings = audit()
    except (OSError, subprocess.CalledProcessError):
        print("legal content audit could not inspect the source tree", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        return 1
    print("legal content audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
