# Changelog

All notable changes to LenkRaster will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases
use semantic versioning once the first public tag is created.

## [Unreleased]

- Updated the pinned release builder to `build` 1.6.0 and grouped Python
  dependency updates across project and lock-file directories.

## [0.1.1] - 2026-08-30

### Changed

- Allow Aseprite exports to create one missing immediate output parent below the trusted
  root while retaining create-only publication and refusing deeper missing trees.

## [0.1.0] - 2026-08-30

### Added

- Initial bounded critic, palette lab, animation QA, golden-corpus runner, CLI, and local
  stdio MCP server.
- Optional, bounded Aseprite CLI export and direct cycle-QA integration.
- Two Aseprite MCP tools and matching CLI/Python APIs.
- Public security policy, contributing guide, release checklist, dependency audit, and
  secret/path artifact checks.

### Changed

- Adopt the pre-release `LenkRaster` identity across the distribution, import package,
  command-line entry points, local MCP server, repository links, and environment variables.
- Replace all previously bundled palettes with four original Ryan Lenk palettes and add
  explicit asset licensing, provenance, third-party notices, and a name-screen record.

### Security

- Require an explicit MCP trusted root for every tool call.
- Require Python 3.10+, Pillow 12.3+, and a current reproducible CI dependency set.
- Validate Aseprite metadata and PNG sheets before publishing or analysis.
- Keep Aseprite invocation fixed, non-shell, time-bounded, and create-only.
- Contain user-owned palette files below an explicit trusted root and validate their
  schema, encoded size, metadata, color count, and unique RGB values before image work.

### Removed

- Private project-specific calibration reports, adapter code, tests, and handoff material
  from the public source tree.
- All externally authored palette data and ambiguous palette provenance; users may supply
  lawfully obtained values through the bounded custom-palette interface.

[Unreleased]: https://github.com/itsryanlenk/lenkraster/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/itsryanlenk/lenkraster/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/itsryanlenk/lenkraster/releases/tag/v0.1.0
