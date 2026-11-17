#!/usr/bin/env python3
"""Measure how an editor's exported file drifts in colour from the editor's own preview,
and produce an ffmpeg lutrgb filter that cancels it.

Usage:
  fit_drift.py <video> --pair TIME=IMAGE [--pair TIME=IMAGE ...] [options]
  fit_drift.py <video> --sheet SHEET --cols C --rows R --times t1,t2,... [options]

  <video>            the exported master (or any file to compare against the preview)
  --pair T=IMG       a preview still taken at timeline second T (screenshot / editor frame grab)
  --sheet ...        one contact-sheet image of preview frames laid out in a C x R grid,
                     row-major, in the same order as --times
  --skip-top F       fraction of each preview image to ignore at the top (labels, UI), default 0.1
  --range auto|tv|full  how to read the video's YUV (default: from its tag)
  --group NAME=RANGES  fit frames inside these timeline ranges separately, e.g.
                     --group photos=12.27-16.87,19.93-21.47  (repeatable). Frames outside
                     every group form the "default" group. Use it when one kind of content
                     drifts differently (Edits: photos under adjustment layers).
  --min-offset X     a group gets a correction only if some channel's mean offset is >= X (default 1.5)
  --out FILE         write the fits and the final "filter" as JSON

Prints per-channel fit preview = a*export + b, the error before/after, per-frame
offsets and an ffmpeg filter (lutrgb, with enable= time ranges when groups are used).
Frames whose offsets sit far from their group's median are flagged as outliers: that
usually means a different kind of content (photo, title, transition) or a misaligned
frame, not noise. Group them or drop them rather than letting them skew the fit.
Standard library only; needs ffmpeg/ffprobe.
"""
import argparse
import json
import subprocess
import sys

W, H = 27, 48  # sample grid; small on purpose so tiny misalignment and compression noise average out


def video_range(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=color_range,pix_fmt", "-of", "default=nw=1", path],
                         capture_output=True, text=True).stdout
    return "full" if ("color_range=pc" in out or "pix_fmt=yuvj" in out) else "tv"


def grab(args_in, vf):
    res = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *args_in, "-frames:v", "1",
                          "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True)
    if res.returncode != 0 or len(res.stdout) != W * H * 3:
        sys.exit(f"frame grab failed: {res.stderr.decode()[:300]}")
    return res.stdout


def fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 1.0
    b = my - a * mx
    e0 = (sum((y - x) ** 2 for x, y in zip(xs, ys)) / n) ** 0.5
    e1 = (sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys)) / n) ** 0.5
    return a, b, mx, my, e0, e1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--pair", action="append", default=[])
    ap.add_argument("--sheet")
    ap.add_argument("--cols", type=int)
    ap.add_argument("--rows", type=int)
    ap.add_argument("--times")
    ap.add_argument("--skip-top", type=float, default=0.1)
    ap.add_argument("--range", default="auto", choices=["auto", "tv", "full"])
    ap.add_argument("--group", action="append", default=[])
    ap.add_argument("--min-offset", type=float, default=1.5)
    ap.add_argument("--out")
    a = ap.parse_args()

    groups = []  # (name, [(start, end), ...])
    for g in a.group:
        name, spec = g.split("=", 1)
        rngs = []
        for part in spec.split(","):
            s, e = part.split("-")
            rngs.append((float(s), float(e)))
        groups.append((name, rngs))

    def group_of(t):
        for name, rngs in groups:
            if any(s <= t < e for s, e in rngs):
                return name
        return "default"

    rng = video_range(a.video) if a.range == "auto" else a.range
    frames = []  # (time, preview_bytes)
    skip = a.skip_top
    crop_prev = f"crop=iw:ih*{1 - skip}:0:ih*{skip}"
    if a.sheet:
        if not (a.cols and a.rows and a.times):
            sys.exit("--sheet needs --cols, --rows and --times")
        times = [float(t) for t in a.times.split(",")]
        for i, t in enumerate(times):
            c, r = i % a.cols, i // a.cols
            vf = (f"crop=iw/{a.cols}:ih/{a.rows}:{c}*iw/{a.cols}:{r}*ih/{a.rows},{crop_prev},"
                  f"scale={W}:{H}:flags=area,format=rgb24")
            frames.append((t, grab(["-i", a.sheet], vf)))
    for p in a.pair:
        t, img = p.split("=", 1)
        frames.append((float(t), grab(["-i", img], f"{crop_prev},scale={W}:{H}:flags=area,format=rgb24")))
    if not frames:
        sys.exit("give --pair or --sheet")

    samples = {}  # group -> list of (t, export_bytes, preview_bytes, offsets)
    n = W * H
    for t, pb in frames:
        vf = (f"scale=in_color_matrix=bt709:in_range={rng},{crop_prev},"
              f"scale={W}:{H}:flags=area,format=rgb24")
        eb = grab(["-ss", str(t), "-i", a.video], vf)
        d = [sum(pb[c::3]) / n - sum(eb[c::3]) / n for c in range(3)]
        samples.setdefault(group_of(t), []).append((t, eb, pb, d))

    print(f"video read as {rng}-range BT.709; {len(frames)} frames compared")
    result = {"groups": {}}
    order = [g for g, _ in groups if g in samples] + (["default"] if "default" in samples else [])
    for gname in order:
        rows = samples[gname]
        print(f"\n== group '{gname}' ({len(rows)} frames)")
        coef, offsets = {}, []
        for ch, name in enumerate("rgb"):
            xs = [v for _, eb, _, _ in rows for v in eb[ch::3]]
            ys = [v for _, _, pb, _ in rows for v in pb[ch::3]]
            k, b, mx, my, e0, e1 = fit(xs, ys)
            coef[name] = [round(k, 4), round(b, 2)]
            offsets.append(my - mx)
            print(f"{name.upper()}: preview = {k:.4f}*export {b:+.2f}   mean export {mx:.1f} preview {my:.1f} "
                  f"(offset {my - mx:+.1f})   rmse {e0:.1f} -> {e1:.1f}")
        med = [sorted(r[3][c] for r in rows)[len(rows) // 2] for c in range(3)]
        print("per-frame offset (preview - export) R/G/B:")
        for t, _, _, d in rows:
            far = max(abs(d[c] - med[c]) for c in range(3))
            flag = "   <- outlier: different content or misaligned?" if far > 4 and len(rows) >= 4 else ""
            print(f"  t={t:>6}: {d[0]:+5.1f} {d[1]:+5.1f} {d[2]:+5.1f}{flag}")
        # A channel whose mean offset is tiny is left untouched: fitting slope/intercept to noise
        # only adds error (seen on real footage: red fitted to 1.03x-4.4 with no real drift).
        for ch, name in enumerate("rgb"):
            if abs(offsets[ch]) < a.min_offset:
                coef[name] = [1.0, 0.0]
        apply = max(abs(o) for o in offsets) >= a.min_offset
        kept = [n.upper() for i, n in enumerate("rgb") if abs(offsets[i]) >= a.min_offset]
        print(f"correction: {'yes, channels ' + '/'.join(kept) if apply else 'no (offsets below --min-offset; leave this group alone)'}")
        result["groups"][gname] = {"coef": coef, "offsets": [round(o, 2) for o in offsets],
                                   "frames": len(rows), "apply": apply}

    def lut(coef, enable=None):
        body = ":".join(f"{c}='clip(val*{coef[c][0]}{coef[c][1]:+},0,255)'" for c in "rgb")
        return f"lutrgb={body}" + (f":enable='{enable}'" if enable else "")

    parts = []
    inside = "+".join(f"between(t,{s},{e})" for _, rngs in groups for s, e in rngs)
    for gname in order:
        g = result["groups"][gname]
        if not g["apply"]:
            continue
        if gname == "default":
            parts.append(lut(g["coef"], f"not({inside})" if inside else None))
        else:
            rngs = dict(groups)[gname]
            parts.append(lut(g["coef"], "+".join(f"between(t,{s},{e})" for s, e in rngs)))
    result["filter"] = ",".join(parts)
    # single-group shortcut keeps the old top-level keys
    if len(order) == 1:
        result.update(result["groups"][order[0]]["coef"])
    print("\nfilter:\n" + (result["filter"] or "(none: no group needs correction)"))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"saved {a.out}")


if __name__ == "__main__":
    main()
