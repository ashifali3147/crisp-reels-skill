# Edits app (metapilot-native MCP)

These notes apply when the reel is edited in the Edits desktop app and driven through the `metapilot-native` MCP tools. They were measured on schema version 2026.10.19-1; re-check the numbers if the app updates.

## Importing footage
- `importMedia` only reads sandbox-reachable paths. Put clips under `~/Movies/...`.
- If you pre-cut clips with ffmpeg, keep them at native resolution (2160x3840 for 4K), 30 fps, `-crf 12`, `-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709`, and always use `ffmpeg -nostdin` inside loops, because ffmpeg otherwise reads the loop's stdin.
- To upgrade an existing project's clips to better sources without losing edits, use `replaceElementMedia` (id, path). It keeps timeline position, length, filters and HSL.

## Before export
- **Frame rate:** new projects default to a 1440x2560 canvas at **60 fps**. A 60 fps export of 30 fps footage duplicates every frame. Run `setFrameRate` with `fps: 30` (check with `getCanvas`). Clip start times snap to the 30 fps grid; at most a 1-frame shift, so beat sync survives.
- The canvas stays 1440x2560 for preview. The export resolution is set separately and renders at that resolution.

## Export settings (exact values the tool accepts)
`setExportSettings` needs every field, otherwise `export` fails with "incomplete preset row":

```
resolutionType: "4K"          # one of 720p | HD | 2K | 4K   (use "HD" for 1080x1920)
resolution:     "2160x3840"   # WxH string
frameRate:      30
codec:          "hevc"        # the export pipeline is HEVC-only; "h264" is rejected
bitrate:        80000000      # bits per second
dynamicRange:   "SDR"
aspectRatio:    "nineBySixteen"
quality:        "high"
```

Then `export` with an absolute path under `~/Movies`, and poll `exportStatus` (use `wait` between polls; a 37 s 4K reel takes about 30 s).

## Known export defects (fix them in the skill's step 4)
- Output is **full range** (`yuvj420p`, `color_range=pc`), with data really spanning 0-255. Convert to TV range in the final encode.
- **Colour drift versus the app's own preview**, measured on one project: red +3, green -4.5, blue -1.5 in the export compared with `frameSheet` frames (preview = 1.025*R-6.4, 1.016*G+2.4, 1.013*B). It reads as warmer and dirtier than what the user approved. Measure it each time rather than reusing these numbers.

- **Photos drift far more than video, and each photo differently.** On one test reel (photos with slow zooms, each under its own colour-grade adjustment layer), the export rendered every photo flatter and lighter than the preview, by +2 to +13 per channel and about +8 on average, while video clips drifted only about +1.5 on green. Get the photo time ranges with `elements` (fields `id,startTime,endTime,sourceMediaType,content`; `sourceMediaType: "photo"`), give each photo its own `--group` in `fit_drift.py`, and take one preview frame inside each photo plus 10+ across the video clips.
- Earlier exports of the same projects showed the same pattern, so this is the app's export path, not a one-off.

## Check the footage resolution first
`probeMedia` with a clip `id` shows the source file's path and size. Old projects may use 1080p footage (one test reel used iPhone 1080p 30 fps clips). Then export the master at `HD` / `1080x1920` with a high bitrate (40 Mbps): exporting at 4K would only upscale. If the clips are 4K, but were downscaled copies, replace them with the originals (`replaceElementMedia`) before exporting.

## Work on a copy
`setFrameRate` changes the user's project. Duplicate it first (from the home screen, `duplicateProject`, then `renameProject` to "<name> (export test)") unless the user wants the original changed.

## Getting preview reference frames
`frameSheet` with `id: ""`, `path: ""`, `times: "1,3,6,..."`, `columns: 6`, `cellWidth: 270` renders the edit as the app shows it. The result's image is saved under `~/.claude/projects/.../tool-results/*.jpg` (and the app's own copy under `~/Library/Containers/com.burbn.basel.mac/.../frame-sheets/`). Each cell has a timestamp label in its top-left corner, so keep `--skip-top 0.1` in `fit_drift.py`. Pass the sheet with `--sheet <file> --cols 6 --rows N --times <same list>`.

## Music
Licensed tracks (`importPath: obfuscatedWindow`) can't be analysed (`audioLevels` fails). Saved Instagram original audio (`importPath: fullFile`) can be, with `audioLevels`/`audioOnsets`. Use `beatMarkers` for the beat grid of either.
