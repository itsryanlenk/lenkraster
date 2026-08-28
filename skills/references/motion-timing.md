# Motion timing profiles

Our own action-timing reference for generated character sprites. Numbers come from our
production pipeline's QA'd output (contacts-heavy walk, moving holds, turn easing) and
standard gamedev practice; they are NOT copied from any third-party preset table.

Influences we cite without copying: PerfectPixel's open preset collection
(github.com/gykim80/perfectpixel-studio) demonstrated the value of a complete
action-coverage matrix for generation prompts; cure's PixelJoint tutorial and Slynyrd's
pixelblogs informed the craft rules elsewhere in this repo. Verify their licenses before
reusing their material; this file only contains ours.

## Loop cycles

| Action | Frames | fps | Timing texture |
|---|---|---|---|
| idle/breathe | 4 | 6 | pingpong, 400ms/frame, head preserved |
| idle-combat | 4 | 8 | tighter bounce, weapon raised |
| walk | 6 | 10 | contacts (f1, f4) ~120ms, passes ~85ms |
| run | 6 | 12 | near-even ~70-85ms, forward lean |
| sprint | 6 | 14 | same poses as run pushed to extremes |
| swim/crawl/climb | 6 | 8 | even rhythm |
| fall | 4 | 10 | loop until landing |
| sleep | 4 | 4 | slowest loop; add Zzz particles not frames |

## One-shot actions

| Action | Frames | fps | Notes |
|---|---|---|---|
| jump | 5 | 10 | anticipation -> launch -> apex -> fall -> pre-land |
| land | 4 | 12 | faster than takeoff; landing weight beats flight detail |
| attack (light) | 5 | 12 | windup frames held 120-200ms each (anticipation) |
| slash/stab/kick | 4-5 | 14 | smear frame replaces in-between at strike moment |
| shoot/dodge | 4 | 14-16 | dodge at 16: urgency lives in playback speed |
| parry | 4 | 16 | shortest window = highest skill |
| hurt | 3 | 10 | damage feedback must be instant; never more than 3f |
| death | 5 | 8 | once, hold last frame |

## Laws encoded above

1. Playback speed carries urgency before frame count does.
2. Nothing needs more than 6 frames per action.
3. hurt is always the shortest action.
4. Landing reads faster than takeoff.
5. Contacts heavy, passes light; ease with uneven durations, not extra frames
   (turn profile: 240 -> 60 -> 90 -> 240 ms).

## Programmatic gates (lenkraster cycle QA)

- analyze rendered premultiplied RGBA, so hidden RGB under alpha zero cannot create motion
- for transparent sprites, visible-pixel min/max ratio >= 0.5 across the selected scope
  (no flicker/empty frames); fully opaque plate/ROI scopes report this gate as N/A
- each authored gesture group must contain at least one transition where the same frame
  pair has rendered MAD >= 15 and >= 4 changed pixels
- first-vs-last MAD is loop-closure telemetry only; a closed loop may correctly return to
  its starting pose with a value of zero
- integrated plates require the subject ROI plus separate transition groups for each
  gesture; bounds use `(x0, y0, x1, y1)` with exclusive maxima
- feet-slide check (roadmap): bottom-row opaque centroid displacement per cycle must
  match move_speed * cycle_duration within ~20%
- contact-weight check (roadmap): contact frame durations > pass frame durations;
  perfectly-even timings flag machine-made feel
