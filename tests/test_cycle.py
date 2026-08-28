"""Animation QA regressions for real sprite loops and integrated plates."""

import numpy as np
import pytest
from PIL import Image

import lenkraster.cycle as cycle
from lenkraster.cycle import load_frames, qa_cycle


def _save(path, rgba):
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(path)
    return str(path)


def _frame(width=16, height=12, *, x=None):
    frame = np.zeros((height, width, 4), dtype=np.uint8)
    if x is not None:
        frame[4:8, x:x + 3, :3] = (230, 120, 50)
        frame[4:8, x:x + 3, 3] = 255
    return frame


def test_closed_loop_can_return_to_first_frame_and_still_move(tmp_path):
    paths = [
        _save(tmp_path / "frame-0.png", _frame(x=2)),
        _save(tmp_path / "frame-1.png", _frame(x=7)),
        _save(tmp_path / "frame-2.png", _frame(x=2)),
    ]

    report = qa_cycle(paths)

    assert report["verdict"] == "PASS"
    assert report["loop_closure_mad"] == 0.0
    assert report["motion_peak_mad"] >= 15.0


def test_cycle_accepts_verified_in_memory_rgba_without_reopening_paths():
    frames = [_frame(x=2), _frame(x=7), _frame(x=2)]

    report = qa_cycle(frames)

    assert report["verdict"] == "PASS"
    assert report["frame_names"] == ["frame-0", "frame-1", "frame-2"]
    assert report["loop_closure_mad"] == 0.0


def test_cycle_rejects_malformed_in_memory_frames():
    malformed = np.zeros((12, 16, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="RGBA"):
        qa_cycle([malformed, malformed])


def test_cycle_rejects_oversized_inputs_before_decode_or_copy(tmp_path, monkeypatch):
    converted = []

    class FakeImage:
        size = (5, 5)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def convert(self, _mode):
            converted.append(True)
            raise AssertionError("oversized path was decoded")

    encoded = tmp_path / "encoded-image.png"
    encoded.write_bytes(b"not-a-real-image")
    monkeypatch.setattr(cycle.Image, "open", lambda _path: FakeImage())
    with pytest.raises(ValueError, match="pixel limit"):
        qa_cycle([str(encoded), str(encoded)], max_frame_pixels=10)
    assert converted == []

    copied = []
    original_array = cycle.np.array

    def tracked_array(*args, **kwargs):
        copied.append(True)
        return original_array(*args, **kwargs)

    monkeypatch.setattr(cycle.np, "array", tracked_array)
    oversized = np.zeros((5, 5, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="pixel limit"):
        qa_cycle([oversized, oversized], max_frame_pixels=10)
    assert copied == []


def test_identical_frames_are_still_reported_as_dead(tmp_path):
    frame = _frame(x=3)
    paths = [_save(tmp_path / f"frame-{i}.png", frame) for i in range(3)]

    report = qa_cycle(paths)

    assert report["verdict"] == "REVIEW"
    assert any("does not move" in issue for issue in report["issues"])


def test_single_pixel_noise_does_not_count_as_animation(tmp_path):
    base = _frame(x=3)
    noisy = base.copy()
    noisy[0, 0] = (255, 0, 255, 255)
    paths = [
        _save(tmp_path / "frame-0.png", base),
        _save(tmp_path / "frame-1.png", noisy),
        _save(tmp_path / "frame-2.png", base),
    ]

    report = qa_cycle(paths)

    assert report["verdict"] == "REVIEW"
    assert report["motion_peak_pixels"] == 1
    assert any("changed pixels" in issue for issue in report["issues"])


def test_full_opaque_plate_motion_is_measured_in_changed_region(tmp_path):
    base = np.full((160, 220, 4), (184, 157, 120, 255), dtype=np.uint8)
    moved = base.copy()
    moved[90:94, 120:124, :3] = (245, 210, 175)
    paths = [
        _save(tmp_path / "plate-0.png", base),
        _save(tmp_path / "plate-1.png", moved),
        _save(tmp_path / "plate-2.png", base),
    ]

    report = qa_cycle(paths)

    assert report["verdict"] == "PASS"
    assert report["active_bounds"] == [120, 90, 124, 94]
    assert report["motion_peak_mad"] >= 15.0
    assert report["visibility_ratio"] is None
    assert report["visibility_scope"] == "not-applicable-opaque-frame"


def test_cycle_rejects_mismatched_frame_dimensions(tmp_path):
    paths = [
        _save(tmp_path / "frame-a.png", _frame(width=16)),
        _save(tmp_path / "frame-b.png", _frame(width=17)),
    ]

    with pytest.raises(ValueError, match="same dimensions"):
        qa_cycle(paths)


def test_cycle_enforces_frame_count_limit(tmp_path):
    paths = [_save(tmp_path / f"frame-{i}.png", _frame(x=i + 1)) for i in range(3)]

    with pytest.raises(ValueError, match="at most 2 frames"):
        qa_cycle(paths, max_frames=2)


def test_cycle_rejects_per_frame_encoded_bytes_before_pillow_opens(
    tmp_path, monkeypatch
):
    path = tmp_path / "oversized.png"
    path.write_bytes(b"x" * 33)
    monkeypatch.setattr(cycle, "_MAX_ENCODED_FRAME_BYTES", 32, raising=False)
    monkeypatch.setattr(cycle, "_MAX_TOTAL_ENCODED_BYTES", 128, raising=False)
    opened = []

    def pillow_must_not_open(_source):
        opened.append(True)
        raise AssertionError("oversized encoded frame reached Pillow")

    monkeypatch.setattr(cycle.Image, "open", pillow_must_not_open)

    with pytest.raises(ValueError, match="encoded byte limit"):
        load_frames([str(path)])

    assert opened == []


def test_cycle_rejects_aggregate_encoded_bytes_before_pillow_opens(
    tmp_path, monkeypatch
):
    paths = [
        _save(tmp_path / "frame-0.png", _frame(x=2)),
        _save(tmp_path / "frame-1.png", _frame(x=7)),
    ]
    sizes = [
        int((tmp_path / f"frame-{index}.png").stat().st_size)
        for index in range(2)
    ]
    monkeypatch.setattr(
        cycle, "_MAX_ENCODED_FRAME_BYTES", max(sizes), raising=False)
    monkeypatch.setattr(
        cycle, "_MAX_TOTAL_ENCODED_BYTES", sum(sizes) - 1, raising=False)
    opened = []
    original_open = cycle.Image.open

    def tracked_open(source):
        opened.append(True)
        return original_open(source)

    monkeypatch.setattr(cycle.Image, "open", tracked_open)

    with pytest.raises(ValueError, match="total encoded byte limit"):
        load_frames(paths)

    assert opened == [True], (
        "the frame that exceeds the aggregate cap must not be parsed")


def test_cycle_path_read_error_does_not_disclose_path(tmp_path):
    path = tmp_path / "private-contact-name.png"

    with pytest.raises(ValueError) as exc_info:
        load_frames([str(path)])

    assert str(tmp_path) not in str(exc_info.value)
    assert path.name not in str(exc_info.value)


@pytest.mark.parametrize("source_kind", ["glob", "iterable"])
def test_absolute_frame_limit_is_checked_before_source_advancement(
    monkeypatch, source_kind
):
    if source_kind == "glob":
        def iglob_must_not_run(_pattern):
            raise AssertionError("glob advanced before absolute frame limit validation")

        monkeypatch.setattr(cycle.glob, "iglob", iglob_must_not_run)
        source = "frame-*.png"
    else:
        class IterableMustNotAdvance:
            def __iter__(self):
                raise AssertionError(
                    "iterable advanced before absolute frame limit validation")

        source = IterableMustNotAdvance()

    with pytest.raises(ValueError, match="absolute safety limit"):
        load_frames(source, max_frames=1_000_000)


def test_cycle_enforces_decoded_pixel_limit(tmp_path):
    paths = [
        _save(tmp_path / "frame-0.png", _frame(width=33, height=33, x=2)),
        _save(tmp_path / "frame-1.png", _frame(width=33, height=33, x=7)),
    ]

    with pytest.raises(ValueError, match="pixel limit"):
        qa_cycle(paths, max_frame_pixels=1024)


def test_motion_thresholds_must_be_met_by_the_same_transition(tmp_path):
    base = np.zeros((10, 10, 4), dtype=np.uint8)
    base[..., 3] = 255
    broad_subtle = base.copy()
    broad_subtle[..., :3] = 1
    tiny_loud = broad_subtle.copy()
    tiny_loud[0, 0, :3] = 255
    paths = [
        _save(tmp_path / "frame-0.png", base),
        _save(tmp_path / "frame-1.png", broad_subtle),
        _save(tmp_path / "frame-2.png", tiny_loud),
    ]

    report = qa_cycle(paths, motion_threshold=100, min_motion_pixels=50)

    assert report["verdict"] == "REVIEW"
    assert report["transition_group_reports"][0]["qualifying_transitions"] == []


def test_motion_threshold_uses_raw_value_before_output_rounding(tmp_path):
    base = np.zeros((5, 5, 4), dtype=np.uint8)
    base[..., 3] = 255
    changed = base.copy()
    changed.reshape(-1, 4)[:24, 0] = 15
    changed.reshape(-1, 4)[24, 0] = 14
    paths = [
        _save(tmp_path / "frame-0.png", base),
        _save(tmp_path / "frame-1.png", changed),
    ]

    report = qa_cycle(paths, motion_threshold=15.0, min_motion_pixels=25)

    assert report["adjacent_mad"] == [15.0]
    assert report["verdict"] == "REVIEW"


def test_alpha_transition_ignores_hidden_rgb_payload(tmp_path):
    visible = np.full((2, 2, 4), (100, 50, 0, 255), dtype=np.uint8)
    hidden_same = visible.copy()
    hidden_same[..., 3] = 0
    hidden_zero = np.zeros_like(visible)
    reports = []
    for dirname, source in (("same", hidden_same), ("zero", hidden_zero)):
        folder = tmp_path / dirname
        folder.mkdir()
        paths = [
            _save(folder / "frame-0.png", source),
            _save(folder / "frame-1.png", visible),
        ]
        reports.append(qa_cycle(paths, motion_threshold=100, min_motion_pixels=4))

    assert reports[0] == reports[1]
    assert reports[0]["transition_group_reports"][0]["verdict"] == "PASS"
    assert reports[0]["adjacent_mad"] == [405.0]


def test_alpha_strength_change_is_meaningful_motion(tmp_path):
    faint = np.full((2, 2, 4), (0, 0, 0, 129), dtype=np.uint8)
    opaque = faint.copy()
    opaque[..., 3] = 255
    paths = [
        _save(tmp_path / "frame-0.png", faint),
        _save(tmp_path / "frame-1.png", opaque),
    ]

    report = qa_cycle(paths, motion_threshold=100, min_motion_pixels=4)

    assert report["verdict"] == "PASS"
    assert report["adjacent_changed_pixels"] == [4]


def test_cycle_enforces_total_decoded_pixel_limit(tmp_path):
    paths = [_save(tmp_path / f"frame-{i}.png", _frame(width=5, height=5, x=1))
             for i in range(3)]

    with pytest.raises(ValueError, match="total decoded pixel limit"):
        qa_cycle(paths, max_frame_pixels=25, max_total_pixels=50)


def test_cycle_enforces_pair_work_limit(tmp_path):
    base = np.zeros((5, 5, 4), dtype=np.uint8)
    base[..., 3] = 255
    white = base.copy()
    white[..., :3] = 255
    red = base.copy()
    red[..., 0] = 255
    paths = [
        _save(tmp_path / "frame-0.png", base),
        _save(tmp_path / "frame-1.png", white),
        _save(tmp_path / "frame-2.png", red),
    ]

    with pytest.raises(ValueError, match="pair-work pixel limit"):
        qa_cycle(paths, max_pair_pixels=74)


def test_glob_is_bounded_before_paths_are_materialized(monkeypatch):
    def eager_glob_must_not_run(_pattern):
        raise AssertionError("eager glob materialized every match")

    monkeypatch.setattr(cycle.glob, "glob", eager_glob_must_not_run)
    monkeypatch.setattr(cycle.glob, "iglob", lambda _pattern: iter(("a", "b", "c")))

    with pytest.raises(ValueError, match="at most 2 frames"):
        load_frames("frame-*.png", max_frames=2)


def test_flicker_issue_uses_safe_frame_labels(tmp_path):
    visible = _frame(x=3)
    empty = _frame()
    paths = [
        _save(tmp_path / "visible.png", visible),
        _save(tmp_path / "empty.png", empty),
    ]

    report = qa_cycle(paths)
    issue_text = " ".join(report["issues"])

    assert str(tmp_path) not in issue_text
    assert "empty.png" in issue_text


def test_report_declares_metric_schema_and_bounds_convention(tmp_path):
    paths = [
        _save(tmp_path / "frame-0.png", _frame(x=2)),
        _save(tmp_path / "frame-1.png", _frame(x=7)),
    ]

    report = qa_cycle(paths)

    assert report["schema_version"] == 2
    assert report["metric_schema"] == "premultiplied-rgba-l1-changed-pixels-v1"
    assert report["bounds_convention"] == "xyxy-exclusive"


def test_roi_scopes_flicker_to_the_subject(tmp_path):
    base = np.zeros((20, 20, 4), dtype=np.uint8)
    base[:10] = (80, 60, 40, 255)
    first = base.copy()
    first[11:13, 10:12] = (230, 120, 50, 255)
    moved = base.copy()
    moved[11:13, 13:15] = (230, 120, 50, 255)
    paths = [
        _save(tmp_path / "frame-0.png", first),
        _save(tmp_path / "frame-1.png", moved),
        _save(tmp_path / "frame-2.png", base),
    ]

    full_report = qa_cycle(paths)
    roi_report = qa_cycle(paths, roi=(10, 10, 15, 15))

    assert full_report["verdict"] == "PASS"
    assert roi_report["verdict"] == "REVIEW"
    assert roi_report["visibility_scope"] == "roi"
    assert any("flicker" in issue for issue in roi_report["issues"])


def test_transition_groups_require_each_authored_group_to_move(tmp_path):
    base = np.zeros((8, 8, 4), dtype=np.uint8)
    base[..., 3] = 255
    walking_1 = base.copy()
    walking_1[2:4, 2:4, :3] = 255
    walking_2 = base.copy()
    walking_2[2:4, 2:4, 0] = 255
    paths = [
        _save(tmp_path / "frame-0.png", base),
        _save(tmp_path / "frame-1.png", base),
        _save(tmp_path / "frame-2.png", base),
        _save(tmp_path / "frame-3.png", walking_1),
        _save(tmp_path / "frame-4.png", walking_2),
    ]

    report = qa_cycle(paths, transition_groups={
        "walking": [(0, 3), (3, 4)],
        "pincer": [(0, 1), (1, 2)],
    })
    groups = {group["name"]: group for group in report["transition_group_reports"]}

    assert report["verdict"] == "REVIEW"
    assert groups["walking"]["verdict"] == "PASS"
    assert groups["pincer"]["verdict"] == "REVIEW"
    assert any("pincer" in issue for issue in report["issues"])


def test_cycle_validates_thresholds_bounds_and_groups(tmp_path):
    path = _save(tmp_path / "frame.png", _frame(x=2))
    paths = [path, path]

    with pytest.raises(ValueError, match="finite"):
        qa_cycle(paths, motion_threshold=float("nan"))
    with pytest.raises(ValueError, match="positive"):
        qa_cycle(paths, motion_threshold=0)
    with pytest.raises(ValueError, match="positive"):
        qa_cycle(paths, min_motion_pixels=0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        qa_cycle(paths, flicker_ratio=1.1)
    with pytest.raises(ValueError, match="positive"):
        qa_cycle(paths, max_total_pixels=0)
    with pytest.raises(ValueError, match="inside frame dimensions"):
        qa_cycle(paths, roi=(0, 0, 17, 12))
    with pytest.raises(ValueError, match="frame index"):
        qa_cycle(paths, transition_groups={"bad": [(0, 2)]})
    with pytest.raises(ValueError, match="safe"):
        qa_cycle(paths, transition_groups={1: [(0, 1)]})


def test_transition_groups_enforce_aggregate_pair_budget(tmp_path):
    paths = [
        _save(tmp_path / "frame-0.png", _frame(x=2)),
        _save(tmp_path / "frame-1.png", _frame(x=7)),
    ]
    repeated_pairs = [(0, 1), (1, 0)]

    with pytest.raises(ValueError, match="aggregate pair budget"):
        qa_cycle(paths, transition_groups={
            "gesture-a": repeated_pairs,
            "gesture-b": repeated_pairs,
            "gesture-c": repeated_pairs,
        })


def test_transition_groups_charge_duplicate_entries_to_aggregate_budget(tmp_path):
    paths = [
        _save(tmp_path / "frame-0.png", _frame(x=2)),
        _save(tmp_path / "frame-1.png", _frame(x=7)),
        _save(tmp_path / "frame-2.png", _frame(x=9)),
    ]

    with pytest.raises(ValueError, match="aggregate pair budget"):
        qa_cycle(paths, transition_groups={
            f"gesture-{index}": [(0, 1)] * 9
            for index in range(9)
        })


def test_public_load_frames_retains_int32_dtype(tmp_path):
    path = _save(tmp_path / "frame.png", _frame(x=2))

    frames, _ = load_frames([path])

    assert frames[0].dtype == np.int32


def test_cycle_requires_at_least_two_frames(tmp_path):
    path = _save(tmp_path / "frame.png", _frame(x=2))

    with pytest.raises(ValueError, match="at least two frames"):
        qa_cycle([path])
