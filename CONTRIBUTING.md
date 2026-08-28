# Contributing

LenkRaster welcomes small, evidence-backed improvements to its deterministic pixel-art
engines and trusted-local interfaces.

## Development setup

Use Python 3.10 or newer in an isolated environment:

```console
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -c requirements/ci.txt -e ".[dev]"
python -m pytest tests -q
```

Before submitting a change, also run:

```console
python -m pip check
python -m pip_audit --skip-editable
python -m bandit -q -r src
python scripts/audit_public_tree.py
python -m build
python scripts/audit_public_tree.py dist
```

## Engineering expectations

- Reproduce defects with a failing test before changing implementation.
- Keep all file, image, animation, JSON, request, and subprocess work explicitly bounded.
- Preserve trusted-root containment, path-free public failures, and create-only outputs.
- Use argument vectors with `shell=False`; never accept arbitrary subprocess flags or
  scripts through MCP.
- Keep the MCP server local stdio only.
- Preserve advisory semantics. A score or cycle result is not art approval.
- Add synthetic fixtures in tests. Never commit private, client-owned, or proprietary art.
- Do not vendor or redistribute Aseprite, its binaries, or licensed sample content.
- Do not add secrets, workstation paths, personal email addresses, or `.env` files.

## Pull requests

Describe the original scenario, the new test, security-boundary impact, and the exact
verification performed. Keep unrelated refactors separate.
