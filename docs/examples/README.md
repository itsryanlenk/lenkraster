# Demo asset provenance

The files in this directory are public demonstration fixtures for LenkRaster. They are
not acceptance evidence and do not imply artwork approval.

- `hammer-cycle-source.png` is a four-frame blacksmith-automaton cycle generated for this
  repository with OpenAI's built-in image-generation tool on 2026-08-27. It used no
  external character, logo, or artwork reference. The generated master was scaled to the
  bounded public strip with Aseprite 1.3.17.2.
- `hammer-cycle-palette.json` is a repository-owned 48-color test target derived from the
  public demo pixels with Pillow's maximum-coverage palette selection. It is a user-palette
  example, not a LenkRaster built-in palette and not an externally sourced palette.
- `hammer-cycle-fixed-palette.png` is the actual LenkRaster 0.1.0 output from quantizing the
  public source strip against that JSON file. LenkRaster reported 48/48 target colors used;
  an independent check confirmed every visible output RGB value belongs to the target.
- `material-ramp.png` and `ordered-dither.png` are actual deterministic LenkRaster CLI
  outputs from the commands shown in the repository README.

The intermediate generated master and local `.aseprite` working document are intentionally
not distributed. No private calibration artwork, local path, Aseprite executable, model
credential, or unrelated generated asset belongs in this directory.
