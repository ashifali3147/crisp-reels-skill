#!/usr/bin/env python3
"""Inspect an exported video master and flag what will hurt it on Instagram.

Usage: diagnose.py <video> [--json]

Reports codec, resolution, real vs. nominal frame rate (duplicated frames),
colour range / matrix, bitrate, HDR flags and audio, then lists problems.
Needs ffprobe and ffmpeg on PATH. Standard library only.
"""
import json
import re
import subprocess
import sys


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path):
    out = run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path
    ])
    if out.returncode != 0:
        sys.exit(f"ffprobe failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def ratio(s):
    try:
        n, d = s.split("/")
        return float(n) / float(d) if float(d) else 0.0
    except Exception:
        return 0.0


def unique_frame_ratio(path, seconds=4.0):
    """Fraction of frames that differ from the previous one over the first few seconds.

    mpdecimate drops near-identical frames, so kept/total close to 0.5 means
    every frame is doubled (e.g. a 30 fps edit exported at 60 fps)."""
    total = run(["ffmpeg", "-nostdin", "-hide_banner", "-t", str(seconds), "-i", path,
                 "-map", "0:v:0", "-f", "null", "-"])
    kept = run(["ffmpeg", "-nostdin", "-hide_banner", "-t", str(seconds), "-i", path,
                "-map", "0:v:0", "-vf", "mpdecimate", "-f", "null", "-"])

    def frames(res):
        m = re.findall(r"frame=\s*(\d+)", res.stderr)
        return int(m[-1]) if m else 0

    t, k = frames(total), frames(kept)
    return (k / t if t else 1.0), t, k


def luma_extremes(path, frames=20):
    """Real min/max luma values, to tell genuinely full-range data from a mislabelled tag."""
    res = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-frames:v", str(frames),
               "-vf", "signalstats,metadata=print", "-f", "null", "-"])
    mins = [int(x) for x in re.findall(r"signalstats\.YMIN=(\d+)", res.stderr)]
    maxs = [int(x) for x in re.findall(r"signalstats\.YMAX=(\d+)", res.stderr)]
    return (min(mins) if mins else None, max(maxs) if maxs else None)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    as_json = "--json" in sys.argv

    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        sys.exit("no video stream found")

    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if abs(rot) == 90:
        w, h = h, w
    fps = ratio(v.get("avg_frame_rate", "0/1"))
    uniq, tot, kept = unique_frame_ratio(path)
    effective_fps = fps * uniq
    rng = v.get("color_range", "unknown")
    ymin, ymax = luma_extremes(path)
    br = int(info["format"].get("bit_rate", 0) or 0)
    dur = float(info["format"].get("duration", 0) or 0)

    r = {
        "file": path,
        "codec": v.get("codec_name"),
        "profile": v.get("profile"),
        "width": w, "height": h,
        "aspect": round(w / h, 4) if h else None,
        "nominal_fps": round(fps, 3),
        "unique_frame_ratio": round(uniq, 3),
        "effective_fps": round(effective_fps, 2),
        "pix_fmt": v.get("pix_fmt"),
        "color_range": rng,
        "color_space": v.get("color_space", "unknown"),
        "color_transfer": v.get("color_transfer", "unknown"),
        "luma_min": ymin, "luma_max": ymax,
        "bitrate_mbps": round(br / 1e6, 2),
        "duration_s": round(dur, 2),
        "audio": ({"codec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 0)),
                   "channels": a.get("channels")} if a else None),
    }

    issues = []
    if r["aspect"] and abs(r["aspect"] - 9 / 16) > 0.01:
        issues.append(f"Not 9:16 ({w}x{h}); Reels will be cropped or letterboxed.")
    if min(w, h) < 1080:
        issues.append(f"Below 1080 wide ({w}x{h}); Instagram will upscale it and it will look soft.")
    if fps > 31 and uniq < 0.9:
        issues.append(f"Nominal {fps:.0f} fps but ~{effective_fps:.0f} unique fps: many frames are duplicated "
                      "(a 30 fps edit exported at 60; low bitrates hide some duplicates from detection). "
                      "Set the editor project to 30 fps and re-export, or drop duplicates with fps=30.")
    elif fps > 31:
        issues.append(f"{fps:.0f} fps; Instagram serves Reels at 30 fps. Convert to 30 for the upload file.")
    if rng == "pc" or (v.get("pix_fmt", "").startswith("yuvj")):
        real_full = ymin is not None and (ymin < 10 or ymax > 245)
        issues.append("Full-range (pc / yuvj) colour. Instagram and many players assume TV range, so it can look "
                      "washed out or crushed. Convert full->tv in the final encode"
                      + (" (data really is full range)." if real_full else " (tag may be wrong: data looks TV-range; check before converting)."))
    if r["color_transfer"] in ("smpte2084", "arib-std-b67"):
        issues.append("HDR transfer (PQ/HLG). Export SDR BT.709 for Instagram unless you intend an HDR upload.")
    if r["color_space"] not in ("bt709", "unknown"):
        issues.append(f"Colour matrix {r['color_space']}; tag/convert to bt709.")
    px = w * h
    min_mbps = 8 if px <= 1080 * 1920 else 25
    if r["bitrate_mbps"] and r["bitrate_mbps"] < min_mbps:
        issues.append(f"Low bitrate {r['bitrate_mbps']} Mbps for {w}x{h}; detail will already be lost before upload.")
    if a and int(a.get("sample_rate", 0)) not in (44100, 48000):
        issues.append(f"Unusual audio sample rate {a.get('sample_rate')}.")
    if not a:
        issues.append("No audio stream.")
    r["issues"] = issues

    if as_json:
        print(json.dumps(r, indent=2))
        return
    print(f"File: {path}")
    print(f"  {r['codec']} {r['profile'] or ''} {w}x{h}  {r['duration_s']}s  {r['bitrate_mbps']} Mbps")
    print(f"  fps nominal {r['nominal_fps']}, unique-frame ratio {r['unique_frame_ratio']} -> effective ~{r['effective_fps']}")
    print(f"  pix_fmt {r['pix_fmt']}, range {rng}, matrix {r['color_space']}, transfer {r['color_transfer']}, luma {ymin}-{ymax}")
    print(f"  audio: {r['audio']}")
    print("Issues:" if issues else "Issues: none found")
    for i in issues:
        print(f"  - {i}")


if __name__ == "__main__":
    main()
