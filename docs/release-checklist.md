# Public release checklist

This checklist covers a GitHub source release. Publishing to a package index is a separate
decision and requires verified package-name ownership and release provenance.

## Source boundary

- [ ] The default branch contains only public, licensed source and synthetic fixtures.
- [ ] No private corpus, project-specific adapter, internal report, handoff file, or local
      client configuration is tracked.
- [ ] Commit author identities are intended for public disclosure.
- [ ] `python scripts/audit_public_tree.py` passes on the tracked tree.
- [ ] A full-history secret scanner passes on every ref intended for publication.

## Verification

- [ ] CI passes on every supported Python/OS matrix entry.
- [ ] `python -m pip check` reports no broken requirements.
- [ ] `python -m pip_audit --skip-editable` reports no known vulnerability.
- [ ] The full test suite passes with the dependency-complete environment.
- [ ] A fresh wheel is installed into a new environment.
- [ ] The installed `lenkraster-mcp` executable completes initialize, tools/list, one
      bounded critique, and a create-only quantization collision check.
- [ ] A generated disposable `.aseprite` animation passes export and direct cycle QA with
      the documented Aseprite version.

## Artifacts and provenance

- [ ] Confirm the distribution is `lenkraster` while the import package remains
      `lenkraster`.
- [ ] Build a fresh sdist and wheel from the reviewed commit.
- [ ] Install `requirements/release.txt` in hash-checking mode and build without dependency
      resolution or build isolation.
- [ ] `python scripts/audit_public_tree.py dist` passes on both archives.
- [ ] Archive members contain no absolute paths, credentials, private art, caches, or
      development environments.
- [ ] Record SHA-256 hashes for the sdist and wheel in the GitHub release.
- [ ] Create the tag from the reviewed commit and attach artifacts through a protected,
      least-privilege workflow.
- [ ] Confirm the `lenkraster` pending publisher targets
      `itsryanlenk/lenkraster`, workflow `release.yml`, and environment `pypi`.
- [ ] Do not publish to PyPI until account recovery, the protected environment, and the
      exact release tag are verified.

## MCP deployment

- [ ] Use an absolute installed-wheel executable path.
- [ ] Set `LENKRASTER_TRUSTED_ROOT` to a disposable sprite workspace, never a source or
      private art repository.
- [ ] Set `LENKRASTER_ASEPRITE_EXECUTABLE` only when the optional bridge is needed.
- [ ] Record and set `LENKRASTER_ASEPRITE_SHA256`; verify the installed Aseprite is a
      supported 1.3.17.2-or-newer build.
- [ ] Run the opt-in real Aseprite compatibility test with a generated disposable fixture.
- [ ] Confirm every original built-in palette has authorship/provenance metadata and that
      no user-supplied palette file is included in release artifacts.
- [ ] Confirm `ASSET_LICENSE.md` and `THIRD_PARTY_NOTICES.md` appear in both the source
      distribution and wheel license directory.
- [ ] Keep stdio local; do not add or expose HTTP transport.
- [ ] Roll back by removing the client MCP entry and deleting only the isolated runtime.
