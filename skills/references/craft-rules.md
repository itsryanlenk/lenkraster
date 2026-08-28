# Craft rules for pixel art (cited)

Every rule states: the law, the source, and the machine check. Thresholds here are the
ones implemented in lenkraster's critic. Sources were read in full; nothing is recalled
from memory.

Sources (read and cached by the authors during a research run, Aug 2026):
- [S1] cure, ["The Pixel Art Tutorial"](https://pixeljoint.com/forum/forum_posts.asp?TID=11299)
  (2010, consolidated from the community's noobtorials and Pixelation Ramblethread).
- [S2] Raymond Schlitter (Slynyrd), "Pixelblog 1: Color Palettes" (2018) and
  "Pixelblog 5: Back to Basics" (2018):
  <https://www.slynyrd.com/blog/2018/1/10/pixelblog-1-color-palettes> and
  <https://www.slynyrd.com/blog/2018/5/16/pixelblog-5-back-to-basics>.
- [S3] Pixelation "Ramblethread" on clusters, as quoted in [S1].
- [S4] [WCAG 2.2 relative luminance and contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), W3C.

## 1. Clusters are the unit of reading

Law: pixels of similar color must touch and form flat readable shapes; an isolated
single pixel is noise ("in the wild, pixels travel in packs") [S1][S3].
Machine check: 4-connected components per exact color; flag when >15% of regions are
1px specks. `critic.check_orphans`

## 2. Manual AA is minimal and placed inside

Law: AA eases jagged edges by hand; too much blurs, lone dots merely blunt corners,
and AA stacked in parallel lines is banding. Keep at most ONE aa line per edge, vary
its lengths, prefer inside corners; AA against the background (sel-out) is a defect
[S1].
Machine check: stacked same-color diagonal runs >=2 deep with intermediate-shade
verification between two other colors. `critic.check_banding`

## 3. Line rhythm

Law: only horizontal, vertical, and 45-degree lines are clean by default; all other
diagonals need uniform run lengths that change by steps of 1 (e.g. 2,2,1 then 3,3,2)
[S1].
Machine check: staircase run-length variance on silhouette edges >=12px.
`critic.check_jaggies`

## 4. No pillow shading

Law: brightness must not follow distance-to-outline evenly; that follows the flat
shape instead of the 3D form. Frontal light is legal if form is still modeled [S1].
Machine check: BFS distance-from-edge vs luminance, flag |r| > 0.55.
`critic.check_pillow`

## 5. Outlines: tinted, not black; subordinate

Law: pure black outlines fight warm palettes and flatten color; and when the outline
exceeds ~half of all opaque pixels the sprite reads as a drawing, not shapes [S1
community practice].
Machine check: black share of outline pixels >50%; outline area >55% of opaque area.
`critic.check_outline`

## 6. Palette discipline

Law: low color counts keep every pixel purposeful; expand only when the piece needs
new shades [S1]. Near-duplicate colors read as dirty pixels.
Machine check: unique colors > sqrt(area)*1.2; RGB-sum distance < 24 pairs.
`critic.check_palette`

## 7. Value separation beats hue count

Law: relative value matters more than hue; adjacent same-hue shades closer than ~7%
value melt together at 1x. Use hue shift for richness instead [S1][S2].
Machine check: per 30-degree hue bucket, adjacent OKLCH L steps < 0.055.
`palette.check_contrast`

## 8. Ramp law (color)

Law: build ramps in a perceptual space; saturation peaks mid-ramp and dies toward
white ("eye burning" otherwise); hue drifts toward dark (Slynyrd shifts up to 20
deg/step; our default -8) [S2]. OKLab constants per Ottosson (bottosson.github.io).
Machine implementation: `palette.make_ramp` (eased L, chroma taper, chroma-search
gamut clamping, never channel clipping).

## 9. Dither is controlled noise

Law: dither creates deliberate independent pixels, so it always flirts with noise;
sparse 25% checkerboard on small canvases reads as dirt [S1]. Reserve for gradients
and transparency fades.
Machine check: 2x2 luminance alternation density > 6%. `critic.check_dither_noise`

## 10. Silhouette and scale

Law: every frame must read from its filled silhouette alone; scale in whole-number
multiples with no scaler AA; NES native 256x240 is the classic reference point;
working canvases 128-256px presented at 2x-4x are typical [S2].
Machine check: opaque area / bbox area < 0.42 flags sparse silhouettes.
`critic.check_silhouette`

## Known false-positive modes

Intentional chunky shading on tiny canvases can trip banding; radial glows trip the
pillow check (glows are legal effects); stylized flat art can trip value-separation.
The critic is a first-pass teacher, not a final judge: when a finding contradicts art
direction at zoom, trust the direction.
