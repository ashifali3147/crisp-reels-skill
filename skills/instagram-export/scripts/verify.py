"""Check an Instagram upload file against the target spec.

Usage: verify.py <file> [--master MASTER]

Checks 9:16, >=1080 wide, H.264, 30 fps with no duplicated frames, TV range
BT.709, yuv420p, bitrate in a sensible band, AAC 48 kHz, faststart (moov
before mdat). With --master, also compares frame count/duration and the
average colour of a few frames (both read with their own range tags) so you
can see the encode did not shift anything. Exit code 1 if a check fails.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from diagnose import probe, ratio, unique_frame_ratio  # noqa: E402


def mean_rgb(path, frame):
    # Select by frame number, not -ss: seeking two differently-encoded files to the same
    # timestamp can land on neighbouring frames, which shows up as a fake colour shift.
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=color_range,pix_fmt", "-of", "default=nw=1", path],
                         capture_output=True, text=True).stdout
    rng = "full" if ("color_range=pc" in out or "pix_fmt=yuvj" in out) else "tv"
    res = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-frames:v", "1",
                          "-vf", f"select=eq(n\\,{frame}),scale=in_color_matrix=bt709:in_range={rng},"
                                 "scale=32:56:flags=area,format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True)
    b = res.stdout
    n = len(b) // 3 or 1
    return [sum(b[c::3]) / n for c in range(3)]


def faststart(path):
    with open(path, "rb") as f:
        head = f.read(1 << 20)
    m, d = head.find(b"moov"), head.find(b"mdat")
    return m != -1 and (d == -1 or m < d)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    master = sys.argv[sys.argv.index("--master") + 1] if "--master" in sys.argv else None
    info = probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    w, h = int(v["width"]), int(v["height"])
    fps = ratio(v.get("avg_frame_rate", "0/1"))
    uniq, _, _ = unique_frame_ratio(path)
    br = int(info["format"].get("bit_rate", 0)) / 1e6
    checks = [
        ("9:16 aspect", abs(w / h - 9 / 16) < 0.01, f"{w}x{h}"),
        (">=1080 wide", w >= 1080, f"{w}"),
        ("H.264", v.get("codec_name") == "h264", v.get("codec_name")),
        ("30 fps", abs(fps - 30) < 0.1, f"{fps:.2f}"),
        ("no duplicated frames", uniq > 0.8, f"unique ratio {uniq:.2f}"),
        ("TV range", v.get("color_range") == "tv", v.get("color_range")),
        ("BT.709", v.get("color_space") == "bt709", v.get("color_space")),
        ("yuv420p", v.get("pix_fmt") == "yuv420p", v.get("pix_fmt")),
        ("bitrate 8-80 Mbps", 8 <= br <= 80, f"{br:.1f} Mbps"),
        ("AAC 48 kHz", bool(a) and a.get("codec_name") == "aac" and a.get("sample_rate") == "48000",
         f"{a.get('codec_name')} {a.get('sample_rate')}" if a else "no audio"),
        ("faststart", faststart(path), ""),
    ]
    ok = True
    for name, passed, detail in checks:
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name:22} {detail}")
    if master:
        mi = probe(master)
        md = float(mi["format"]["duration"]); od = float(info["format"]["duration"])
        same = abs(md - od) < 0.1
        ok &= same
        print(f"[{'PASS' if same else 'FAIL'}] {'duration matches':22} master {md:.2f}s, file {od:.2f}s")
        mfps = ratio(next(s for s in mi["streams"] if s["codec_type"] == "video").get("avg_frame_rate", "30/1"))
        if abs(mfps - fps) > 0.1:
            print("       (master and file frame rates differ; colour spot-check skipped, use fit_drift.py)")
        else:
            for t in (od * 0.15, od * 0.5, od * 0.85):
                n = int(t * fps)
                m, o = mean_rgb(master, n), mean_rgb(path, n)
                d = [o[i] - m[i] for i in range(3)]
                print(f"       frame {n:5d} ({t:5.1f}s) colour diff file-master R{d[0]:+.1f} G{d[1]:+.1f} B{d[2]:+.1f}")
            print("       (near 0 unless a drift correction was applied on purpose; the authoritative check is "
                  "fit_drift.py against preview stills)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
