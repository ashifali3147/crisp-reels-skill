---
name: instagram-export
description: Turn an edited video into an Instagram Reel upload file that keeps its quality - diagnose the editor's export (resolution, duplicated 60fps frames, full-range colour, bitrate, HDR), measure and cancel the colour drift between the editor's preview and its export, encode a 1080x1920 H.264 file (plus a 4K alternative) in one pass with ffmpeg, verify it, and give the upload checklist. Use this whenever someone is exporting or uploading a reel/short/TikTok-style vertical video and worries about quality - "my reel looks blurry/pixelated after export", "looks great in CapCut/Edits/Premiere/VN but bad on Instagram", "best export settings for reels", "make this Instagram-ready", "export in 4K for insta" - even if they don't mention ffmpeg. Export and delivery only; not for colour grading or editing the cut.
compatibility: Needs ffmpeg and ffprobe on PATH and python3 (standard library only). The Edits-app section needs the metapilot-native MCP server.
---

# Instagram export

Most "my reel looked great in the editor and bad after export" problems are not Instagram's fault. They come from a few mistakes in the export chain. Each one costs quality, and they stack. This skill finds which ones are present, fixes them, and produces one clean upload file, so Instagram's own compression starts from the best possible input.

What usually goes wrong:

| Problem | Why it hurts |
|---|---|
| Footage downscaled early (4K to 1080 before or during editing), then upscaled by a 1440/2160 canvas | Detail is thrown away, then the softness gets baked in |
| Project at 60 fps with 30 fps footage | Every frame is duplicated, so half the bitrate is wasted; Instagram serves 30 fps anyway |
| Full-range (pc / yuvj420p) output | Players and Instagram assume TV range, so the result looks washed out or crushed and blotchy |
| Export colour differs from the editor's preview | The editor's render path shifts colour (often warmer, less green), so "it looked right in the app" |
| Re-encoding an already compressed file several times | Each generation adds blocking and smears fine texture |
| Low bitrate (< ~8 Mbps at 1080p) | Instagram re-encodes from a file that's already damaged |

Target upload file: **1080x1920, 9:16, 30 fps, H.264 High, ~15-20 Mbps, TV-range BT.709, AAC 48 kHz, faststart.** Guides agree on 1080p 30 fps H.264; Instagram doesn't serve true 4K Reels. A clean downscale from a 4K master keeps the most detail. A 4K file is offered as an alternative, because some creators find Instagram's own downscale slightly sharper. Let the user test both.

Scripts are in `scripts/` next to this file. Run them with `python3` / `bash` and absolute paths.

## Workflow

### 1. Get the best master the editor can produce

Ask what editor they use and how the footage was shot (4K? fps?). The goal is a high-bitrate master at the footage's real resolution and 30 fps. The 1080p upload file comes later, from this master.

- In the editor: set the project or timeline to **30 fps** (unless the footage and intent are really 24/60). Export at the highest resolution the footage supports (4K if shot in 4K), the highest bitrate or "high quality" setting, SDR, and HEVC or H.264.
- If you also prepare the source clips yourself (trimming or speed changes with ffmpeg before importing), keep them at the native resolution, with near-lossless settings (`-crf 12`, tv range, bt709 tags). Never downscale them to 1080 "to save space". That's the most common hidden cause of softness.
- **Edits app (metapilot-native MCP):** read `references/edits-app.md`. It has specific traps: 60 fps default canvas, HEVC-only export, full-range output, measurable colour drift, and exact `setExportSettings` values.

### 2. Diagnose the master

```bash
python3 scripts/diagnose.py /path/to/master.mp4
```

It reports resolution, nominal versus effective fps (duplicate frames), colour range including the actual luma extremes (so a mislabelled tag is caught), matrix, HDR, bitrate and audio, then lists the issues in plain words. Fix at the source what can only be fixed there: a low-resolution or low-bitrate master means re-exporting from the editor. Range, fps duplicates, matrix tags and audio rate get fixed in step 4.

### 3. Measure colour drift against the editor's preview (when the user says the export looks different, or the editor is known to drift)

The editor's preview is the reference, because that's what the user approved. Get a few preview stills at known timeline times (6-12 spread across the edit, different scenes). Use editor frame grabs if the editor can produce them (Edits: `frameSheet`). Otherwise use screenshots of the paused preview, cropped to the video area. Then:

```bash
python3 scripts/fit_drift.py master.mp4 --pair 3=prev_03s.png --pair 9=prev_09s.png ... --out drift.json
# or one grid image of frames, row-major in the order of --times:
python3 scripts/fit_drift.py master.mp4 --sheet sheet.png --cols 6 --rows 2 --times 1,3,6,9,12.5,16,19.5,22,25,29,31,33.8 --out drift.json
```

It fits `preview = a*export + b` per channel and prints the mean offset, the per-frame offsets, the error before and after, and a ready filter. Channels whose mean offset is below `--min-offset` (default 1.5) are left untouched, because fitting noise only adds error. Apply a correction only when the drift is real: the same sign across most frames, and the error dropping clearly. `--skip-top` (default 0.1) ignores timestamp labels or UI at the top of each still. Screenshots taken with Night Shift / True Tone or HDR display effects are not a valid reference, so ask the user to turn those off or use editor frame grabs.

**Read the per-frame offsets before trusting the fit.** If some frames sit far from the rest (the script flags them as outliers), those frames are usually a different kind of content that the editor renders differently: photos, titles, frames under adjustment layers or effects. They're rarely misalignment. One global correction would then be wrong for both kinds. Find what the outlier frames have in common (ask the editor for clip types and positions), then fit them separately with `--group NAME=start-end,...`. Frames outside every group are fitted as `default`, and the output filter switches each correction on only for its time ranges. If each item in a group drifts by a different amount (each photo has its own adjustment), give each item its own group. Still images need only one reference frame each.

Always hold out a second set of preview stills at different moments and run `fit_drift.py` on the encoded file against them. That's the only real proof the correction generalises rather than just matching the frames it was fitted on. On a test reel, the held-out offsets for photos went from about +8 to under +1.5, and for video from +1.5 to +0.4.

### 4. Encode the upload files in one pass from the master

```bash
bash scripts/encode.sh master.mp4 /path/to/export ProjectName [--lut "$(python3 -c 'import json;print(json.load(open("drift.json"))["filter"])')"]
```

It converts full range to TV range when needed, drops duplicated frames with `fps=30`, downscales with lanczos, applies the drift correction in RGB if one is given, tags BT.709, and writes `<name>_INSTAGRAM_1080p.mp4` (the upload file) and `<name>_INSTAGRAM_4K.mp4` (only when the master is really 4K). Each is encoded once, straight from the master. Never chain encodes from a previous upload file.

### 5. Verify before handing over

```bash
python3 scripts/verify.py export/Name_INSTAGRAM_1080p.mp4 --master master.mp4
```

All checks should PASS. Its file-versus-master colour line is only a spot check: it can read about 1-2 levels apart even when nothing is wrong, from 8-bit range-conversion rounding. If you applied a correction, the check that counts is `fit_drift.py` on the new file against held-out preview stills: the offsets should sit near 0 (within about ±1). Also look at a 1:1 crop of a detailed, fast-moving frame (foliage, water) to confirm there's no blocking:

```bash
ffmpeg -ss 10 -i Name_INSTAGRAM_1080p.mp4 -frames:v 1 -vf "crop=540:540:0:0" crop.png
```

If an older export exists, put the same crop side by side so the user can see the difference.

### 6. Hand over with the upload checklist

Tell the user which file to upload (the 1080p one) and what the 4K one is for. Then give the checklist. These steps matter as much as the encode:

1. **Instagram → Settings → Media quality → turn on "Upload at highest quality".** Without it, Instagram compresses heavily on upload.
2. **Move the file to the phone without recompression:** AirDrop, cable, or a cloud drive set to original quality. Never send it through WhatsApp or Telegram, which recompress.
3. **Upload on strong Wi-Fi.** Weak connections trigger lower-quality processing.
4. **Don't judge right after posting.** A new Reel can look soft for a few minutes while Instagram finishes processing.
5. If the 1080p upload still looks soft after processing, try the 4K file once and compare.

## Reporting

Report what was wrong and what changed, in plain words with numbers: "export was 60 fps with doubled frames, now 30", "full range converted to TV range", "green was 4.5 lower than the preview, corrected; now within 0.5". Then give the file paths, the verify results, and the checklist. If you didn't measure something (for example, no preview stills were available for drift), say so instead of implying it was checked.
