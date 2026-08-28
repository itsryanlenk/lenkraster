"""lenkraster CLI."""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

from .aseprite import (
    export_document as export_aseprite_document,
    qa_document as qa_aseprite_document,
)
from .critic import critique, critique_many
from .cycle import qa_cycle
from .palette import (available_palettes, check_contrast, dither_image,
                      hex2rgb, load_palette, load_palette_file, make_ramp,
                      quantize_file)
from .shadow import canonical_shadow_json, run_shadow_manifest


def _cmd_critique(args):
    files = args.files
    if len(files) == 1:
        rep = dict(critique(files[0]))
        rep["file"] = os.path.basename(rep["file"])
        if args.json:
            open(args.json, "w").write(json.dumps(rep, indent=1))
            print("wrote", os.path.basename(args.json))
        print(json.dumps(rep, indent=1))
    else:
        res = critique_many(files)
        for report in res["reports"]:
            report["file"] = os.path.basename(report["file"])
        if args.json:
            open(args.json, "w").write(json.dumps(res, indent=1))
            print("wrote", os.path.basename(args.json))
        for r in res["reports"]:
            checks = [f["check"] for f in r["findings"]]
            print(f"{os.path.basename(r['file']):24s} {r['score']:.2f}  {checks}")


def _cmd_ramp(args):
    ramp = make_ramp(args.color, args.stops, args.drift)
    print(" ".join(ramp))
    if args.out:
        sw = 32
        img = np.zeros((sw, sw * len(ramp), 3), dtype=np.uint8)
        for i, hx in enumerate(ramp):
            img[:, i * sw:(i + 1) * sw] = hex2rgb(hx).astype(np.uint8)
        Image.fromarray(img).save(args.out)
        print("wrote", os.path.basename(args.out))


def _cmd_dither(args):
    img = dither_image(args.a, args.b, args.size, args.order)
    out = args.out or "dither_out.png"
    Image.fromarray(img).save(out)
    print("wrote", os.path.basename(out))


def _cmd_check(args):
    res = check_contrast(args.file)
    print(json.dumps(res, indent=1))


def _cmd_quantize(args):
    if args.palette_file is not None:
        if args.root is None:
            raise ValueError("custom palette requires a trusted root")
        colors, _metadata = load_palette_file(
            args.palette_file,
            trusted_root=args.root,
        )
        out, n, used = quantize_file(
            args.file,
            None,
            args.out,
            palette_file=args.palette_file,
            palette_root=args.root,
        )
    else:
        colors, _metadata = load_palette(args.palette)
        out, n, used = quantize_file(args.file, args.palette, args.out)
    print(f"wrote {os.path.basename(out)}; "
          f"{n}/{len(colors)} palette colors used")
    if args.verbose:
        print(" ".join(used))


def _cmd_palettes(_args):
    for name in available_palettes():
        colors, meta = load_palette(name)
        author = f" by {meta['author']}" if meta.get("author") else ""
        print(f"{name:20s} {len(colors):2d} colors{author}")


def _cmd_cycle(args):
    res = qa_cycle(
        args.frames,
        motion_threshold=args.motion_threshold,
        min_motion_pixels=args.min_motion_pixels,
        max_frames=args.max_frames,
        max_frame_pixels=args.max_frame_pixels,
    )
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["verdict"] == "PASS" else 1)


def _cmd_aseprite_export(args):
    report = export_aseprite_document(
        args.document,
        args.out_dir,
        trusted_root=args.root,
        tag=args.tag,
        layer=args.layer,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _cmd_aseprite_cycle(args):
    report = qa_aseprite_document(
        args.document,
        trusted_root=args.root,
        tag=args.tag,
        layer=args.layer,
        motion_threshold=args.motion_threshold,
        min_motion_pixels=args.min_motion_pixels,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    raise SystemExit(0 if report["verdict"] == "PASS" else 1)


def _cmd_shadow(args):
    report = run_shadow_manifest(args.manifest, args.manifest_sha256)
    print(canonical_shadow_json(report))
    raise SystemExit(0 if report["automated_verdict"] == "BASELINE_MATCH" else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lenkraster",
                                 description="Deterministic pixel-art engines.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("critique", help="score sprite(s) against craft rules")
    p.add_argument("files", nargs="+")
    p.add_argument("--json")

    p = sub.add_parser("ramp", help="hue-shifted OKLCH material ramp")
    p.add_argument("--color", required=True)
    p.add_argument("--stops", type=int, default=5)
    p.add_argument("--drift", type=float, default=-8.0,
                   help="hue degrees per step toward dark (negative = shadows blue/purple)")
    p.add_argument("--out")

    p = sub.add_parser("dither", help="ordered Bayer blend of two colors")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--size", type=int, default=48)
    p.add_argument("--order", type=int, default=4, choices=(2, 4))
    p.add_argument("--out")

    p = sub.add_parser("check", help="same-hue contrast report")
    p.add_argument("--file", required=True)

    p = sub.add_parser("quantize", help="snap image to a fixed palette (OKLab nearest)")
    p.add_argument("file")
    palette_source = p.add_mutually_exclusive_group(required=True)
    palette_source.add_argument("--palette", choices=available_palettes())
    palette_source.add_argument(
        "--palette-file",
        help="user-owned palette JSON below --root",
    )
    p.add_argument("--root", help="trusted root required with --palette-file")
    p.add_argument("--out")
    p.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("palettes", help="list built-in palettes")

    p = sub.add_parser("cycle", help="animation cycle QA (exit 1 on REVIEW)")
    p.add_argument("frames", help="glob pattern of frame PNGs")
    p.add_argument("--motion-threshold", type=float, default=15.0)
    p.add_argument("--min-motion-pixels", type=int, default=4)
    p.add_argument("--max-frames", type=int, default=64)
    p.add_argument("--max-frame-pixels", type=int, default=4_194_304)

    p = sub.add_parser(
        "aseprite-export",
        help="export a trusted local Aseprite document to a new sheet/manifest directory",
    )
    p.add_argument("document", help=".ase/.aseprite path below --root")
    p.add_argument("--root", required=True, help="trusted local sprite workspace")
    p.add_argument("--out-dir", required=True, help="new output directory below --root")
    p.add_argument("--tag", help="optional Aseprite frame tag")
    p.add_argument("--layer", help="optional Aseprite layer")

    p = sub.add_parser(
        "aseprite-cycle",
        help="transiently export and QA a trusted local Aseprite animation",
    )
    p.add_argument("document", help=".ase/.aseprite path below --root")
    p.add_argument("--root", required=True, help="trusted local sprite workspace")
    p.add_argument("--tag", help="optional Aseprite frame tag")
    p.add_argument("--layer", help="optional Aseprite layer")
    p.add_argument("--motion-threshold", type=float, default=15.0)
    p.add_argument("--min-motion-pixels", type=int, default=4)

    p = sub.add_parser(
        "shadow", help="run a hash-bound advisory calibration manifest"
    )
    p.add_argument("manifest", help="bounded local shadow manifest JSON")
    p.add_argument("--manifest-sha256", required=True, help="pinned lowercase SHA-256")

    args = ap.parse_args(argv)
    handlers = {
        "critique": _cmd_critique,
        "ramp": _cmd_ramp,
        "dither": _cmd_dither,
        "check": _cmd_check,
        "quantize": _cmd_quantize,
        "palettes": _cmd_palettes,
        "cycle": _cmd_cycle,
        "aseprite-export": _cmd_aseprite_export,
        "aseprite-cycle": _cmd_aseprite_cycle,
        "shadow": _cmd_shadow,
    }
    try:
        handlers[args.cmd](args)
    except SystemExit:
        raise
    except Exception:
        print("lenkraster: command failed", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
