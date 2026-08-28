"""Palette engine tests: ramps, round-trip color math, quantization, contrast."""
import json

import numpy as np
import pytest
from PIL import Image

import lenkraster.palette as palette_module
from lenkraster.palette import (available_palettes, check_contrast, dither_image,
                                hex2rgb, load_palette, load_palette_file, make_ramp,
                                oklab_to_rgb, quantize_file, rgb2hex, rgb_to_oklab)


def test_oklab_round_trip():
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, size=(64, 64, 3)).astype(np.float64)
    back = oklab_to_rgb(rgb_to_oklab(rgb))
    assert np.abs(back - np.round(rgb)).max() <= 1.5  # within rounding + gamut noise


def test_ramp_is_monotonic_light_and_hue_drifts():
    ramp = make_ramp("#d77643", stops=5, drift=-8.0)
    assert len(ramp) == 5
    labs = rgb_to_oklab(np.array([hex2rgb(h) for h in ramp]))
    Ls = labs[:, 0]
    assert np.all(np.diff(Ls) > 0), "ramp must go dark to light"
    # hue should drift monotonically (toward blue/purple = increasing hue angle here)
    hues = np.degrees(np.arctan2(labs[:, 2], labs[:, 1])) % 360
    assert abs(hues[-1] - hues[0]) > 5, "drift must move hue"


def test_ramp_gamut_safe():
    for base in ("#ff0000", "#0000ff", "#00ff00", "#ffffff", "#000000"):
        ramp = make_ramp(base, stops=4)
        for h in ramp:
            c = hex2rgb(h)
            assert c.min() >= 0 and c.max() <= 255


def test_ramp_rejects_unbounded_stop_count():
    with pytest.raises(ValueError, match="at most 64"):
        make_ramp("#d77643", stops=65)


def test_built_in_palettes_load():
    names = available_palettes()
    assert names == [
        "lenk-cinder-16",
        "lenk-fern-4",
        "lenk-signal-16",
        "lenk-studio-32",
    ]
    expected_counts = {
        "lenk-cinder-16": 16,
        "lenk-fern-4": 4,
        "lenk-signal-16": 16,
        "lenk-studio-32": 32,
    }
    for name, count in expected_counts.items():
        colors, meta = load_palette(name)
        assert len(colors) == count
        assert meta["author"] == "Ryan Lenk"


def _install_palette_fixture(tmp_path, monkeypatch, raw):
    palette_root = tmp_path / "palettes"
    palette_root.mkdir()
    (palette_root / "lenk-fern-4.json").write_bytes(raw)
    monkeypatch.setattr(palette_module, "_DATA_DIR", str(palette_root))
    return palette_root


def test_palette_names_are_a_fixed_exact_inventory_and_cannot_traverse(
    tmp_path, monkeypatch
):
    raw = json.dumps({
        "name": "host file",
        "author": "attacker",
        "colors": ["000000", "ffffff"],
    }).encode("utf-8")
    palette_root = tmp_path / "palettes"
    palette_root.mkdir()
    (tmp_path / "outside.json").write_bytes(raw)
    (palette_root / "rogue.json").write_bytes(raw)
    monkeypatch.setattr(palette_module, "_DATA_DIR", str(palette_root))

    assert "rogue" not in available_palettes()
    for name in ("rogue", "../outside", "lenk-fern-4.json", "LENK-FERN-4", "lenk-fern-4 "):
        with pytest.raises(KeyError, match="palette"):
            load_palette(name)


def test_palette_read_is_bounded_before_json_parsing(tmp_path, monkeypatch):
    maximum = 16 * 1024
    raw = (b'{"name":"Test","author":"User","colors":["000000","ffffff"]}'
           + b" " * maximum)
    _install_palette_fixture(tmp_path, monkeypatch, raw)

    def reject_json_parse(*_args, **_kwargs):
        raise AssertionError("oversized palette reached JSON parsing")

    monkeypatch.setattr(palette_module.json, "loads", reject_json_parse)

    with pytest.raises(ValueError, match="byte limit"):
        load_palette("lenk-fern-4")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"name": "Test", "author": "User", "colors": ["000000", "ffffff"],
         "extra": True},
        {"name": "Test", "colors": ["000000", "ffffff"]},
        {"name": "", "author": "", "colors": ["000000", "ffffff"]},
        {"name": "n" * 129, "author": "", "colors": ["000000", "ffffff"]},
        {"name": "Test", "author": "a" * 129,
         "colors": ["000000", "ffffff"]},
        {"name": "Test", "author": "\U0001f980" * 65,
         "colors": ["000000", "ffffff"]},
        {"name": "Test", "author": "bad\u0000author",
         "colors": ["000000", "ffffff"]},
        {"name": "Test", "author": "User", "colors": "000000"},
        {"name": "Test", "author": "User", "colors": []},
        {"name": "Test", "author": "User",
         "colors": [f"{value:06x}" for value in range(65)]},
        {"name": "Test", "author": "User", "colors": ["000000", "000000"]},
        {"name": "Test", "author": "User", "colors": ["#000000", "ffffff"]},
        {"name": "Test", "author": "User", "colors": ["000000", 123456]},
    ],
)
def test_palette_schema_colors_and_metadata_are_strictly_bounded(
    tmp_path, monkeypatch, payload
):
    _install_palette_fixture(
        tmp_path,
        monkeypatch,
        json.dumps(payload).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="invalid"):
        load_palette("lenk-fern-4")


def test_palette_rejects_duplicate_json_keys(tmp_path, monkeypatch):
    raw = (b'{"name":"Test","name":"other","author":"User",'
           b'"colors":["000000","ffffff"]}')
    _install_palette_fixture(tmp_path, monkeypatch, raw)

    with pytest.raises(ValueError, match="invalid"):
        load_palette("lenk-fern-4")


def test_user_palette_file_loads_only_below_trusted_root(tmp_path):
    palette_dir = tmp_path / "palettes"
    palette_dir.mkdir()
    palette_file = palette_dir / "user-owned.json"
    palette_file.write_text(json.dumps({
        "name": "User-owned compatibility palette",
        "author": "Local user",
        "colors": ["102030", "f0e0d0"],
    }), encoding="utf-8")

    colors, metadata = load_palette_file(
        "palettes/user-owned.json",
        trusted_root=tmp_path,
    )

    assert colors == ["#102030", "#f0e0d0"]
    assert metadata == {
        "name": "User-owned compatibility palette",
        "author": "Local user",
    }


def test_user_palette_file_rejects_escape_and_invalid_data_path_free(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({
        "name": "Outside",
        "author": "Local user",
        "colors": ["000000", "ffffff"],
    }), encoding="utf-8")

    with pytest.raises(ValueError) as escaped:
        load_palette_file("../outside.json", trusted_root=trusted)
    assert str(escaped.value) == "user palette is outside trusted root"
    assert str(tmp_path) not in str(escaped.value)

    invalid = trusted / "invalid.json"
    invalid.write_text('{"name":"Bad","author":"User","colors":[]}', encoding="utf-8")
    with pytest.raises(ValueError) as malformed:
        load_palette_file("invalid.json", trusted_root=trusted)
    assert str(malformed.value) == "user palette data is invalid"
    assert str(tmp_path) not in str(malformed.value)


def test_quantize_roundtrip(tmp_path):
    img = (np.random.default_rng(3).integers(0, 256, (32, 32, 4))).astype(np.uint8)
    img[..., 3] = 255
    p = tmp_path / "in.png"
    Image.fromarray(img).save(p)
    out, n, used = quantize_file(str(p), "lenk-fern-4")
    from PIL import Image as I
    snapped = np.array(I.open(out).convert("RGB"))
    fern = [hex2rgb(c) for c in load_palette("lenk-fern-4")[0]]
    for px in snapped.reshape(-1, 3):
        assert any(np.abs(px.astype(int) - color.astype(int)).sum() < 1e-6 for color in fern)


def test_quantize_preserves_palette_png_transparency(tmp_path):
    src = Image.new("P", (4, 3))
    src.putpalette([0, 0, 0, 255, 255, 255] + [0, 0, 0] * 254)
    src.putdata([0, 1, 1, 0] * 3)
    source = tmp_path / "indexed-transparent.png"
    target = tmp_path / "quantized.png"
    src.save(source, transparency=0)

    quantize_file(str(source), "lenk-fern-4", str(target))

    out = Image.open(target).convert("RGBA")
    assert np.array(out.getchannel("A")).ravel().tolist() == [0, 255, 255, 0] * 3


def test_quantize_rejects_decoded_pixel_limit(tmp_path):
    source = tmp_path / "too-large.png"
    Image.new("RGBA", (5, 5), (120, 80, 40, 255)).save(source)

    with pytest.raises(ValueError, match="pixel limit"):
        quantize_file(str(source), "lenk-fern-4", max_pixels=24)


def test_quantize_rejects_encoded_byte_limit_before_pillow(tmp_path, monkeypatch):
    source = tmp_path / "oversized.png"
    with source.open("wb") as stream:
        stream.seek(16 * 1024 * 1024)
        stream.write(b"\0")

    def reject_pillow_open(*_args, **_kwargs):
        raise AssertionError("oversized encoded image reached Pillow")

    monkeypatch.setattr(palette_module.Image, "open", reject_pillow_open)

    with pytest.raises(ValueError, match="byte limit"):
        quantize_file(str(source), "lenk-fern-4")


def test_quantize_discards_hidden_rgb_and_reports_only_visible_colors(tmp_path):
    clean = np.zeros((5, 5, 4), dtype=np.uint8)
    clean[1:4, 1:4] = (180, 90, 45, 255)
    dirty = clean.copy()
    hidden = dirty[..., 3] == 0
    yy, xx = np.mgrid[0:5, 0:5]
    dirty[..., 0][hidden] = ((xx * 47 + yy * 13) % 256)[hidden]
    dirty[..., 1][hidden] = ((xx * 19 + yy * 71) % 256)[hidden]
    dirty[..., 2][hidden] = ((xx * 83 + yy * 29) % 256)[hidden]
    clean_path = tmp_path / "clean.png"
    dirty_path = tmp_path / "dirty.png"
    clean_out = tmp_path / "clean-out.png"
    dirty_out = tmp_path / "dirty-out.png"
    Image.fromarray(clean, "RGBA").save(clean_path)
    Image.fromarray(dirty, "RGBA").save(dirty_path)

    _, clean_n, clean_used = quantize_file(str(clean_path), "lenk-signal-16", str(clean_out))
    _, dirty_n, dirty_used = quantize_file(str(dirty_path), "lenk-signal-16", str(dirty_out))

    assert clean_out.read_bytes() == dirty_out.read_bytes()
    assert (clean_n, clean_used) == (dirty_n, dirty_used)
    rendered = np.array(Image.open(dirty_out).convert("RGBA"))
    assert np.all(rendered[..., :3][rendered[..., 3] == 0] == 0)


def test_quantize_accepts_user_owned_palette_file(tmp_path):
    source = tmp_path / "source.png"
    output = tmp_path / "custom.png"
    palette_file = tmp_path / "user-palette.json"
    Image.new("RGB", (2, 1), (180, 90, 45)).save(source)
    palette_file.write_text(json.dumps({
        "name": "User palette",
        "author": "Local user",
        "colors": ["000000", "ffffff"],
    }), encoding="utf-8")

    result, count, used = quantize_file(
        str(source),
        None,
        str(output),
        palette_file="user-palette.json",
        palette_root=tmp_path,
    )

    assert result == str(output)
    assert count == 1
    assert used[0] in ("#000000", "#ffffff")


def test_contrast_flags_mush_and_passes_clean():
    # two shades 2 units apart in the same hue family -> flagged
    img = np.zeros((24, 24, 4), dtype=np.uint8)
    img[:12] = (100, 60, 40, 255)
    img[12:] = (102, 62, 42, 255)
    res = check_contrast(img)
    assert len(res["weak"]) >= 1
    # strong steps -> clean
    img2 = np.zeros((24, 24, 4), dtype=np.uint8)
    img2[:12] = (90, 50, 35, 255)
    img2[12:] = (220, 150, 95, 255)
    res2 = check_contrast(img2)
    assert res2["weak"] == []


def test_contrast_ignores_rgb_hidden_under_transparency():
    rgba = np.zeros((4, 4, 4), dtype=np.uint8)
    rgba[..., 3] = 0
    rgba[:2, :, :3] = (150, 20, 20)
    rgba[2:, :, :3] = (152, 21, 21)

    report = check_contrast(rgba)

    assert report == {"summary": "fewer than 2 significant colors", "weak": []}


def test_contrast_rejects_decoded_pixel_limit():
    rgba = np.zeros((5, 5, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="pixel limit"):
        check_contrast(rgba, max_pixels=24)


def test_contrast_rejects_encoded_byte_limit_before_pillow(tmp_path, monkeypatch):
    source = tmp_path / "oversized.png"
    with source.open("wb") as stream:
        stream.seek(16 * 1024 * 1024)
        stream.write(b"\0")

    def reject_pillow_open(*_args, **_kwargs):
        raise AssertionError("oversized encoded image reached Pillow")

    monkeypatch.setattr(palette_module.Image, "open", reject_pillow_open)

    with pytest.raises(ValueError, match="byte limit"):
        check_contrast(str(source))


def test_contrast_checks_array_limit_before_float_copy(monkeypatch):
    rgba = np.zeros((5, 5, 4), dtype=np.uint8)
    original_asarray = palette_module.np.asarray

    def reject_float_copy(value, *args, **kwargs):
        if kwargs.get("dtype") is np.float64:
            raise AssertionError("float copy occurred before the safety check")
        return original_asarray(value, *args, **kwargs)

    monkeypatch.setattr(palette_module.np, "asarray", reject_float_copy)

    with pytest.raises(ValueError, match="pixel limit"):
        check_contrast(rgba, max_pixels=24)


def test_dither_output_shape():
    img = dither_image("#000000", "#ffffff", size=32)
    assert img.shape == (32, 32, 3)
    assert set(np.unique(img)) <= {0, 255}


def test_dither_rejects_decoded_pixel_limit():
    with pytest.raises(ValueError, match="pixel limit"):
        dither_image("#000000", "#ffffff", size=1025)
