---
name: pixel-color-ramps
description: "Use to build palettes and ramps for pixel art with lenkraster."
metadata:
  author: ryanlenk
  license: MIT
---

# Pixel color ramps via lenkraster

Tool-agnostic instructions for any agent: MCP tools (`make_ramp`, `palette_quantize`,
`contrast_report`) if connected, otherwise CLI. Color math is Ottosson's OKLab; ramp
recipes follow Slynyrd's published method; citations in `references/craft-rules.md`.

## When to use

Choosing a constrained palette, building material ramps, fixing muddy/flat generated
colors, or snapping sprites to fixed palettes.

## Workflow

1. One material = one ramp = 3-5 stops at sprite scale (up to 9 for hero materials).
2. Build it: `lenkraster ramp --color <base> --stops 5 --drift -8`.
   drift -8 = shadows walk blue/purple, highlights walk warm (default convention).
   Use up to -20 for strong cross-ramp harmony; positive inverts (cool highlights).
3. Verify separation: `lenkraster check --file sprite.png`. Any same-hue pair under dL 0.055
   will melt at 1x; rebuild the ramp or push hues apart.
4. Constrain one file: `lenkraster quantize frame.png --palette lenk-studio-32 --out snapped.png`.
   Repeat with the exact same fixed palette and settings for every frame.
5. ANIMATION LAW: every frame must target the SAME fixed palette. LenkRaster does not
   learn a joint palette across a cycle; visually replay the result for threshold flips
   that could read as color boiling.

## Palette sources

Built-ins are `lenk-cinder-16`, `lenk-fern-4`, `lenk-signal-16`, and `lenk-studio-32`.
`lenkraster palettes` lists them. A user may instead provide a strictly validated palette
JSON below the trusted root with CLI `--palette-file` or MCP `palette_file`. External
palette values are not bundled; independently obtained, permitted values use that
user-owned file interface.

## Rules of thumb encoded here

- Never clip RGB channels to fit gamut; reduce chroma instead.
- Chroma peaks mid-ramp; whites go creamy, blacks stay tinted, never pure.
- Value contrast carries readability; hue contrast is seasoning.
- Dither only for gradients/fades; on small canvases it reads as dirt.
