"""Adversarial + control tests for the lenkraster critic."""
import numpy as np
import pytest
from PIL import Image

import lenkraster.critic as critic
from lenkraster.critic import critique


def _save(arr, tmp_path, name):
    p = str(tmp_path / name)
    Image.fromarray(arr).save(p)
    return p


def test_bad_sprite_scores_low_and_flags_expected_checks(tmp_path):
    S = 64
    img = np.zeros((S, S, 4), dtype=np.uint8)
    yy, xx = np.mgrid[0:S, 0:S]
    d = np.sqrt((yy - S // 2) ** 2 + (xx - S // 2) ** 2)
    mask = d < 24
    lum = np.clip(220 - d * 6, 30, 255)  # radial gradient == pillow shading
    img[..., 0] = lum * 0.9
    img[..., 1] = lum * 0.5
    img[..., 2] = lum * 0.3
    img[..., 3] = np.where(mask, 255, 0)
    for x, y in [(3, 3), (10, 4), (50, 7), (5, 40)]:  # orphan specks
        img[y, x] = (200, 200, 100, 255)
    path = _save(img, tmp_path, "bad.png")

    rep = critique(path)
    checks = {f["check"] for f in rep["findings"]}
    assert rep["score"] < 0.6
    assert "orphan_pixels" in checks
    assert "pillow_shading" in checks
    assert "palette_bloat" in checks
    assert rep["retry_hints"], "retry hints must be present for findings"


def test_clean_control_scores_perfect(tmp_path):
    S = 48
    img = np.zeros((S, S, 4), dtype=np.uint8)
    yy, xx = np.mgrid[0:S, 0:S]
    d = np.sqrt((yy - S // 2) ** 2 + (xx - S // 2) ** 2)
    body = d < 18
    hi = (d < 10) & (xx < S // 2)  # one light direction
    img[body] = (120, 60, 40, 255)
    img[hi] = (210, 130, 80, 255)
    path = _save(img, tmp_path, "good.png")

    rep = critique(path)
    assert rep["score"] >= 0.99
    assert rep["findings"] == []


def test_smooth_circle_not_flagged_for_banding(tmp_path):
    """Correct negative: a smooth radial blob has no stacked AA ridges."""
    S = 64
    img = np.zeros((S, S, 4), dtype=np.uint8)
    yy, xx = np.mgrid[0:S, 0:S]
    d = np.sqrt((yy - S // 2) ** 2 + (xx - S // 2) ** 2)
    mask = d < 24
    lum = np.clip(220 - d * 6, 30, 255)
    img[..., 0] = lum * 0.9
    img[..., 1] = lum * 0.5
    img[..., 2] = lum * 0.3
    img[..., 3] = np.where(mask, 255, 0)
    rep = critique(_save(img, tmp_path, "circle.png"))
    assert "banding" not in {f["check"] for f in rep["findings"]}


def test_stacked_intermediate_diagonals_are_reported_as_banding():
    """A normal bounded scan still recognizes stacked intermediate AA lines."""
    side = 16
    yy, xx = np.indices((side, side))
    diagonals = yy + xx
    arr = np.zeros((side, side, 4), dtype=np.uint8)
    arr[diagonals < 10] = (32, 32, 32, 255)
    arr[(diagonals == 10) | (diagonals == 11)] = (128, 128, 128, 255)
    arr[diagonals > 11] = (224, 224, 224, 255)

    findings = critic.check_banding(arr)

    assert [finding["check"] for finding in findings] == ["banding"]


def test_banding_scan_fails_closed_when_full_frame_work_budget_is_exhausted(
    monkeypatch,
):
    """Many significant colors cannot each trigger an unbounded full-frame mask."""
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[:2] = (32, 32, 32, 255)
    arr[2:] = (224, 224, 224, 255)
    one_full_frame_scan = arr.shape[0] * arr.shape[1]
    monkeypatch.setattr(
        critic, "_MAX_BANDING_MASK_PIXELS", one_full_frame_scan, raising=False
    )
    monkeypatch.setattr(critic, "CHECKS", [critic.check_banding])

    report = critique(arr)

    assert report["score"] == 0.0
    assert report["findings"] == [
        {
            "check": "check_banding_error",
            "severity": 1.0,
            "detail": "RuntimeError: critic check failed",
            "locs": [],
            "hint": "Critic check failed; treat this report as invalid.",
        }
    ]
    assert report["retry_hints"] == [
        "Critic check failed; treat this report as invalid."
    ]


def test_score_floor():
    """Score never goes below zero no matter how many findings pile up."""
    from lenkraster.critic import load
    arr = np.zeros((16, 16, 4), dtype=np.int32)
    arr[::2, ::2, :3] = (250, 254, 251)  # checkerboard noise
    arr[..., 3] = 255
    rep = critique(arr)
    assert 0.0 <= rep["score"] <= 1.0


def test_checkerboard_dither_is_reported():
    yy, xx = np.indices((16, 16))
    checker = ((yy + xx) % 2).astype(np.uint8)
    arr = np.zeros((16, 16, 4), dtype=np.uint8)
    arr[..., :3] = np.where(checker[..., None] == 0, 32, 224)
    arr[..., 3] = 255

    rep = critique(arr)

    assert "heavy_dither" in {finding["check"] for finding in rep["findings"]}


def test_truecolor_critique_does_not_compare_every_color_pair(monkeypatch):
    """A truecolor sprite must not trigger O(unique_colors**2) numpy calls."""
    side = 64
    color_count = side * side
    ids = np.arange(color_count, dtype=np.uint16).reshape(side, side)
    arr = np.zeros((side, side, 4), dtype=np.uint8)
    arr[..., 0] = ids >> 8
    arr[..., 1] = ids & 0xFF
    arr[..., 2] = (ids * 37) & 0xFF
    arr[..., 3] = 255

    original_abs = critic.np.abs
    abs_calls = 0

    def bounded_abs(*args, **kwargs):
        nonlocal abs_calls
        abs_calls += 1
        if abs_calls > color_count * 8:
            raise AssertionError("palette comparison work exceeded a linear budget")
        return original_abs(*args, **kwargs)

    monkeypatch.setattr(critic.np, "abs", bounded_abs)
    rep = critique(arr)

    assert not any(f["check"].endswith("_error") for f in rep["findings"])
    assert abs_calls <= color_count * 8


def test_near_duplicate_scan_caps_actual_python_comparisons(monkeypatch):
    colors = np.array(
        [(red, green, 0) for red in range(6) for green in range(6)],
        dtype=np.uint8,
    )
    original_abs = abs
    comparisons = 0

    def counted_abs(value):
        nonlocal comparisons
        comparisons += 1
        return original_abs(value)

    monkeypatch.setattr(critic, "abs", counted_abs, raising=False)

    with pytest.raises(RuntimeError, match="work limit"):
        critic._near_duplicate_pairs(colors, max_l1=1, max_comparisons=10)

    assert comparisons <= 10


def test_repeated_pixels_are_not_weak_value_stops():
    """Pixel frequency must not turn two flat hue families into weak ramps."""
    arr = np.zeros((20, 20, 4), dtype=np.uint8)
    arr[:, :10] = (200, 0, 0, 255)
    arr[:, 10:] = (0, 200, 200, 255)

    rep = critique(arr)

    assert "weak_value_steps" not in {f["check"] for f in rep["findings"]}


def test_hidden_transparent_rgb_is_removed_before_checks(monkeypatch):
    """RGB payload beneath fully transparent pixels is not visible artwork."""
    dirty = np.zeros((24, 24, 4), dtype=np.uint8)
    dirty[8:16, 8:16] = (120, 80, 40, 255)
    yy, xx = np.mgrid[0:24, 0:24]
    checker = ((yy + xx) % 2).astype(np.uint8) * 255
    hidden = dirty[..., 3] == 0
    dirty[..., 0][hidden] = checker[hidden]
    dirty[..., 1][hidden] = 255 - checker[hidden]
    original = dirty.copy()
    checked_pixels = []

    def capture_pixels(px):
        checked_pixels.append(px.copy())
        return []

    monkeypatch.setattr(critic, "CHECKS", [capture_pixels])
    rep = critique(dirty)

    assert rep["findings"] == []
    assert np.all(checked_pixels[0][hidden, :3] == 0)
    assert np.array_equal(dirty, original), "critique must not mutate the caller's array"


def test_check_exception_fails_closed_and_is_visible(monkeypatch):
    def check_boom(_px):
        raise RuntimeError("private-source://critic-secret")

    monkeypatch.setattr(critic, "CHECKS", [check_boom])
    rep = critique(np.zeros((4, 4, 4), dtype=np.uint8))

    assert rep["score"] == 0.0
    assert rep["findings"] == [
        {
            "check": "check_boom_error",
            "severity": 1.0,
            "detail": "RuntimeError: critic check failed",
            "locs": [],
            "hint": "Critic check failed; treat this report as invalid.",
        }
    ]
    assert rep["retry_hints"] == ["Critic check failed; treat this report as invalid."]


def test_critique_rejects_decoded_pixel_limit():
    art = np.zeros((5, 5, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="pixel limit"):
        critique(art, max_pixels=24)


def test_critic_rejects_encoded_bytes_before_pillow_opens(tmp_path, monkeypatch):
    path = tmp_path / "oversized.png"
    path.write_bytes(b"x" * 33)
    monkeypatch.setattr(critic, "_MAX_ENCODED_IMAGE_BYTES", 32, raising=False)
    opened = []

    def pillow_must_not_open(_source):
        opened.append(True)
        raise AssertionError("oversized encoded image reached Pillow")

    monkeypatch.setattr(critic.Image, "open", pillow_must_not_open)

    with pytest.raises(ValueError, match="encoded byte limit"):
        critic.load(path)

    assert opened == []


def test_critic_path_read_error_does_not_disclose_path(tmp_path):
    path = tmp_path / "private-contact-name.png"

    with pytest.raises(ValueError) as exc_info:
        critic.load(path)

    assert str(tmp_path) not in str(exc_info.value)
    assert path.name not in str(exc_info.value)


@pytest.mark.parametrize("shape, message", [
    ((5, 5, 4), "pixel limit"),
    ((5, 5, 3), "shape"),
])
def test_critique_validates_ndarray_before_int32_copy(monkeypatch, shape, message):
    art = np.zeros(shape, dtype=np.uint8)
    original_asarray = critic.np.asarray
    int32_copies = []

    def tracked_asarray(value, *args, **kwargs):
        requested_dtype = kwargs.get("dtype", args[0] if args else None)
        if requested_dtype is not None and np.dtype(requested_dtype) == np.dtype(np.int32):
            int32_copies.append(True)
        return original_asarray(value, *args, **kwargs)

    monkeypatch.setattr(critic.np, "asarray", tracked_asarray)

    with pytest.raises(ValueError, match=message):
        critique(art, max_pixels=24)

    assert int32_copies == []
