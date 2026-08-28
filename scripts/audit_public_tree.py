#!/usr/bin/env python3
"""Fail closed on common secret, private-path, and unsafe-archive disclosures."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import zipfile


MAX_AUDIT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Signature:
    name: str
    expression: re.Pattern[bytes]


SIGNATURES = (
    Signature(
        "windows-user-path",
        re.compile(rb"(?i)[A-Z]:[\\/](?:Users|Documents)[\\/]"),
    ),
    Signature(
        "macos-user-path",
        re.compile(b"/" + b"Users" + rb"/[^/\s]+/"),
    ),
    Signature(
        "unix-home-path",
        re.compile(b"/" + b"home" + rb"/[^/\s]+/"),
    ),
    Signature(
        "private-key",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    Signature("aws-access-key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    Signature(
        "github-token",
        re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ),
    Signature("openai-key", re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{16,}")),
    Signature("slack-token", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}")),
)

SENSITIVE_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
SENSITIVE_NAMES = {".env", "credentials", "credentials.json", "id_rsa", "id_ed25519"}


def _unsafe_member_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    return (
        not normalized
        or pure.is_absolute()
        or any(part in ("", ".", "..") for part in pure.parts)
        or bool(re.match(r"(?i)^[a-z]:", normalized))
    )


def _scan_bytes(label: str, raw: bytes, findings: list[tuple[str, str]]) -> None:
    if len(raw) > MAX_AUDIT_BYTES:
        findings.append(("file-exceeds-audit-limit", label))
        return
    for signature in SIGNATURES:
        if signature.expression.search(raw):
            findings.append((signature.name, label))


def _scan_name(label: str, findings: list[tuple[str, str]]) -> None:
    path = PurePosixPath(label.replace("\\", "/"))
    name = path.name.lower()
    if path.suffix.lower() in SENSITIVE_SUFFIXES or name in SENSITIVE_NAMES:
        findings.append(("sensitive-filename", label))
    if name.startswith(".env.") and name != ".env.example":
        findings.append(("sensitive-filename", label))


def _tracked_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [root / item.decode("utf-8") for item in completed.stdout.split(b"\x00") if item]


def _scan_file(path: Path, label: str, findings: list[tuple[str, str]]) -> None:
    _scan_name(label, findings)
    try:
        size = path.stat().st_size
        if size > MAX_AUDIT_BYTES:
            findings.append(("file-exceeds-audit-limit", label))
            return
        raw = path.read_bytes()
    except OSError:
        findings.append(("file-unavailable", label))
        return
    _scan_bytes(label, raw, findings)


def _scan_zip(path: Path, findings: list[tuple[str, str]]) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                label = f"{path.name}:{info.filename}"
                if _unsafe_member_name(info.filename):
                    findings.append(("unsafe-archive-member", label))
                    continue
                _scan_name(label, findings)
                if info.is_dir():
                    continue
                if info.file_size > MAX_AUDIT_BYTES:
                    findings.append(("file-exceeds-audit-limit", label))
                    continue
                _scan_bytes(label, archive.read(info), findings)
    except (OSError, zipfile.BadZipFile):
        findings.append(("invalid-archive", path.name))


def _scan_tar(path: Path, findings: list[tuple[str, str]]) -> None:
    try:
        with tarfile.open(path, mode="r:*") as archive:
            for member in archive.getmembers():
                label = f"{path.name}:{member.name}"
                if _unsafe_member_name(member.name):
                    findings.append(("unsafe-archive-member", label))
                    continue
                _scan_name(label, findings)
                if not member.isfile():
                    continue
                if member.size > MAX_AUDIT_BYTES:
                    findings.append(("file-exceeds-audit-limit", label))
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    findings.append(("file-unavailable", label))
                    continue
                _scan_bytes(label, stream.read(MAX_AUDIT_BYTES + 1), findings)
    except (OSError, tarfile.TarError):
        findings.append(("invalid-archive", path.name))


def audit(root: Path, targets: list[Path]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if not targets:
        for path in _tracked_files(root):
            if path.is_file():
                _scan_file(path, path.relative_to(root).as_posix(), findings)
        return findings

    for target in targets:
        if target.is_dir():
            paths = sorted(path for path in target.rglob("*") if path.is_file())
        else:
            paths = [target]
        for path in paths:
            suffixes = [suffix.lower() for suffix in path.suffixes]
            if path.suffix.lower() in (".whl", ".zip"):
                _scan_zip(path, findings)
            elif suffixes[-2:] == [".tar", ".gz"] or path.suffix.lower() in (".tgz", ".tar"):
                _scan_tar(path, findings)
            else:
                _scan_file(path, path.name, findings)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", type=Path, help="artifact files/directories")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        findings = audit(root, [target.resolve() for target in args.targets])
    except (OSError, subprocess.CalledProcessError):
        print("public audit could not inspect the source tree", file=sys.stderr)
        return 2
    if findings:
        for category, label in sorted(set(findings)):
            print(f"{category}: {label}", file=sys.stderr)
        return 1
    print("public tree audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
