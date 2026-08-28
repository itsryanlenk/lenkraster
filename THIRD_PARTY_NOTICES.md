# Third-party notices

LenkRaster is MIT licensed. It does not vendor the projects below, but it depends on or
interoperates with them. Their names identify compatibility and provenance only; no
affiliation or endorsement is claimed.

## Runtime dependencies

- **NumPy** is installed separately under its modified BSD license. Project and license:
  <https://numpy.org/> and <https://numpy.org/doc/stable/license.html>.
- **Pillow** is installed separately under the MIT-CMU license. Project and license:
  <https://python-pillow.github.io/> and
  <https://github.com/python-pillow/Pillow/blob/main/LICENSE>.

LenkRaster wheels declare these dependencies but do not embed their source or binary
packages. A user's package installer resolves them independently.

## Optional Aseprite integration

**Aseprite** is an optional, separately installed native application. LenkRaster invokes
its documented batch command-line interface through a fixed argument vector. It does not
download, bundle, or redistribute Aseprite. Official CLI documentation and licensing FAQ:
<https://www.aseprite.org/docs/cli/> and <https://www.aseprite.org/faq/>.

Aseprite and its marks belong to their respective owners. LenkRaster is not affiliated
with or endorsed by Aseprite or Igara Studio S.A.

## Published methods and craft references

- The OKLab/OKLCH conversion follows Bjorn Ottosson's published mathematical description:
  <https://bottosson.github.io/posts/oklab/>. The implementation in this repository is
  project-authored; no reference source code or images are vendored.
- The critique heuristics cite community pixel-art guidance in
  [`skills/references/craft-rules.md`](skills/references/craft-rules.md). Citations are
  attribution for ideas and terminology, not copied code, artwork, presets, or palettes.

## Repository-generated media

The public demonstration images are governed by [`ASSET_LICENSE.md`](ASSET_LICENSE.md).
OpenAI tooling used to create some source imagery and Aseprite used to export it are not
part of this distribution.
