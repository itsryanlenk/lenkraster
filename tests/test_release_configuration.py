"""Regression checks for release and CI compatibility declarations."""

import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_numpy_ci_pins_cover_every_supported_python_generation():
    """Keep pins installable across the declared Python 3.10-3.14 matrix."""
    constraints = (REPO_ROOT / "requirements" / "ci.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    numpy_constraints = [
        line.strip() for line in constraints if line.strip().startswith("numpy==")
    ]

    assert numpy_constraints == [
        'numpy==2.2.6; python_version < "3.11"',
        'numpy==2.4.2; python_version >= "3.11" and python_version < "3.12"',
        'numpy==2.5.2; python_version >= "3.12"',
    ]


def test_ci_secret_scan_can_read_pull_request_commits():
    """Dependabot PR tokens must let gitleaks enumerate the PR commits."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    top_level_configuration = workflow.split("\nconcurrency:", maxsplit=1)[0]
    required_permissions = "permissions:\n  contents: read\n  pull-requests: read\n"

    assert required_permissions in top_level_configuration


def test_distribution_uses_the_cleared_lenkraster_identity():
    """Every executable package surface must use the new pre-release identity."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_table = pyproject.split("[project]\n", maxsplit=1)[1].split(
        "\n[", maxsplit=1
    )[0]

    assert 'name = "lenkraster"' in project_table.splitlines()
    assert 'lenkraster = "lenkraster.cli:main"' in pyproject
    assert 'lenkraster-mcp = "lenkraster.mcp_main:main"' in pyproject
    assert (REPO_ROOT / "src" / "lenkraster" / "mcp_server.py").is_file()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pip install lenkraster" in readme
    assert (REPO_ROOT / "docs" / "assets" / "lenkraster-readme-banner.png").is_file()
    assert (REPO_ROOT / "docs" / "assets" / "lenkraster-social-banner.png").is_file()


def test_legacy_brand_and_third_party_palettes_are_absent_from_public_tree():
    """The publishable tree must not ship the retired name or palette identities."""
    forbidden = (
        "pixel" + "forge",
        "endes" + "ga",
        "commodore" + "-64",
        "game" + "boy",
        "dawn" + "bringer",
        "pico" + "-8",
    )
    extensions = {".md", ".py", ".toml", ".yml", ".yaml", ".json", ".in", ".txt"}

    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    for encoded in tracked:
        if not encoded:
            continue
        relative = Path(encoded.decode("utf-8"))
        path = REPO_ROOT / relative
        lowered_name = relative.as_posix().lower()
        assert all(term not in lowered_name for term in forbidden), relative
        if path.suffix.lower() not in extensions:
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert all(term not in text for term in forbidden), relative


def test_asset_and_third_party_licensing_ship_with_the_project():
    """Repository-authored assets and external dependencies need explicit notices."""
    asset_license = (REPO_ROOT / "ASSET_LICENSE.md").read_text(encoding="utf-8")
    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    clearance = (REPO_ROOT / "docs" / "name-clearance.md").read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "MIT License" in asset_license
    assert "Ryan Lenk" in asset_license
    assert "Aseprite" in notices
    assert "NumPy" in notices
    assert "Pillow" in notices
    assert "LenkRaster" in clearance
    assert "not a legal opinion" in clearance
    assert "include ASSET_LICENSE.md" in manifest
    assert "include THIRD_PARTY_NOTICES.md" in manifest


def test_legal_content_audit_is_mandatory_and_passes():
    """Authorship/provenance policy must be executable in CI and release builds."""
    audit = REPO_ROOT / "scripts" / "audit_legal_content.py"
    assert audit.is_file()
    completed = subprocess.run(
        [sys.executable, str(audit)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    for workflow_name in ("ci.yml", "release.yml"):
        workflow = (
            REPO_ROOT / ".github" / "workflows" / workflow_name
        ).read_text(encoding="utf-8")
        assert "python scripts/audit_legal_content.py" in workflow


def test_release_workflow_matches_the_trusted_publisher_contract():
    """Publishing stays explicit, owner-scoped, pinned, and tokenless."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "release.yml"

    assert workflow_path.is_file()
    workflow = workflow_path.read_text(encoding="utf-8")
    publish_job = workflow.split("\n  publish:\n", maxsplit=1)[1]

    assert "  workflow_dispatch:\n" in workflow
    assert "  release:\n    types: [published]\n" in workflow
    assert "\npermissions:\n  contents: read\n" in workflow
    assert "      name: pypi\n" in publish_job
    assert "https://pypi.org/p/lenkraster" in publish_job
    assert "      id-token: write\n" in publish_job
    assert workflow.count("id-token: write") == 1
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in workflow
    assert "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33" in publish_job
    assert "python -m pip install --require-hashes -r requirements/release.txt" in workflow
    assert "python -m pip install --no-deps --no-build-isolation -e ." in workflow
    assert "python -m build --no-isolation" in workflow
    assert "pip install --upgrade" not in workflow
    assert "secrets." not in workflow
    assert "password:" not in workflow


def test_release_dependencies_are_transitively_pinned_and_hashed():
    """The release job must not resolve mutable transitive dependencies."""
    lock_path = REPO_ROOT / "requirements" / "release.txt"

    assert lock_path.is_file()
    lock = lock_path.read_text(encoding="utf-8")
    for requirement in (
        "bandit==1.9.4",
        "build==1.5.0",
        "numpy==2.5.2",
        "pillow==12.3.0",
        "pip==26.2.1",
        "pip-audit==2.10.1",
        "pytest==9.1.1",
        "setuptools==84.0.0",
    ):
        assert requirement in lock
    assert lock.count("--hash=sha256:") >= 8
    assert " @ file:" not in lock
    assert "C:\\Users\\" not in lock
    assert "--index-url=https://pypi.org/simple" in lock
    assert "--no-index" not in lock
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include requirements *.in *.txt" in manifest.splitlines()


def test_readme_links_render_from_the_pypi_project_page():
    """Long-description links cannot rely on a repository-relative base URL."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"!?\[[^]]*\]\(([^)]+)\)", readme)

    assert targets
    assert all(target.startswith(("https://", "mailto:", "#")) for target in targets)
