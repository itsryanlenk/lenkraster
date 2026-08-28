---
name: pixel-craft-critique
description: "Use to critique or fix pixel-art sprite quality with lenkraster."
metadata:
  author: ryanlenk
  license: MIT
---

# Pixel craft critique via lenkraster

Tool-agnostic instructions for any agent. If the `lenkraster` MCP server is connected,
prefer its tools; otherwise run the CLI (`pip install lenkraster`). Full rule
citations
live in `references/craft-rules.md`.

## When to use

Reviewing generated or hand-made sprites for craft violations, writing retry-hint
feedback loops for image generation, or deciding whether a bad frame needs a recovery
pass or a full regenerate.

## Workflow

1. Run the critic: MCP `critique_sprite` / CLI `lenkraster critique sprite.png`.
2. Read `score` and `findings`. Score = 1 - sum(severity)/4, except a crashed check
   forces score 0. Scores are comparable only within a calibrated artifact class.
3. Feed `retry_hints` back into your generation prompt VERBATIM if regenerating. They
   are written as model-readable corrections ("keep at most ONE aa line per edge...").
4. For animation frames, also run `qa_cycle` on the frame glob. For integrated plates,
   call the Python API with a tight ROI and explicit groups for every authored gesture.
   REVIEW verdicts list which visibility or motion-group gate failed.
5. Fix colors last: `contrast_report` finds melting shades; `make_ramp` builds legal
   replacements; `palette_quantize` snaps to a retained built-in or trusted user-owned
   fixed palette.

## Decision ladder

- accepted baseline with no new findings: candidate for human review, not automatic ship.
- uncalibrated score, textured card, or full scene: advisory only; never use as a gate.
- banding/pillow findings: fix AA placement by editing pixels, do not regenerate.
- palette_bloat/near_duplicate: quantize or hand-merge colors.
- weak_value_steps: rebuild the material ramp with make_ramp (drift -8), re-snap.
- orphan_pixels on generated sheets: usually JPEG noise -> clean pass first, re-critique.

## Calibration warning

The critic is a first-pass teacher for native sprites and tight crops. Intentional chunky
shading trips banding; radial glows trip pillow; stylized flat art can trip value-separation.
It rejects images above 1,048,576 decoded pixels and is not calibrated for textured
true-color scene plates. When a finding contradicts art direction at zoom, trust the
direction and record the accepted baseline and exception.
