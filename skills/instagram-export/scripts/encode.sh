# Make Instagram upload files from a high-quality master in a single encode each.
#
# Usage: encode.sh <master> <out_dir> <base_name> [--lut "<lutrgb filter>"] [--no-4k] [--fps N]
#
# Writes:
#   <base>_INSTAGRAM_1080p.mp4  H.264 High 4.2, 1080x1920, CRF 14 (cap 20 Mbps), the file to upload
#   <base>_INSTAGRAM_4K.mp4     H.264 High 5.2, 2160x3840, CRF 15 (cap 60 Mbps), only if master is >= 2160 wide
# Both: TV-range BT.709 tags, 30 fps (or --fps), AAC 320k 48 kHz, +faststart.
# Full-range masters are converted to TV range; duplicated 60 fps frames are dropped by fps=30.
set -euo pipefail

master="${1:?master}"; out="${2:?out_dir}"; base="${3:?base_name}"; shift 3
lut=""; want4k=1; fps=30
while [ $# -gt 0 ]; do
  case "$1" in
    --lut) lut="$2"; shift 2;;
    --no-4k) want4k=0; shift;;
    --fps) fps="$2"; shift 2;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
mkdir -p "$out"

info=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height,color_range,pix_fmt -of default=nw=1 "$master")
w=$(echo "$info" | sed -n 's/^width=//p'); h=$(echo "$info" | sed -n 's/^height=//p')
in_range=tv
if echo "$info" | grep -qE 'color_range=pc|pix_fmt=yuvj'; then in_range=full; fi
short=$(( w < h ? w : h ))

# RGB stage only when a drift correction is applied; otherwise stay in YUV and let one scale do range+size.
if [ -n "$lut" ]; then
  pre="fps=$fps,scale=in_color_matrix=bt709:in_range=$in_range:flags=accurate_rnd+full_chroma_int,format=gbrp,$lut,"
  scale_in="out_color_matrix=bt709:out_range=tv"
else
  pre="fps=$fps,"
  scale_in="in_color_matrix=bt709:in_range=$in_range:out_color_matrix=bt709:out_range=tv"
fi
tags=(-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709)
audio=(-c:a aac -b:a 320k -ar 48000)

enc() { # $1 WxH  $2 crf  $3 maxrate  $4 level  $5 outfile
  ffmpeg -nostdin -v error -y -i "$master" \
    -vf "${pre}scale=${1/x/:}:${scale_in}:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p" \
    -c:v libx264 -preset slow -crf "$2" -maxrate "$3" -bufsize "$(( ${3%M} * 2 ))M" \
    -profile:v high -level "$4" -g $((fps * 2)) "${tags[@]}" "${audio[@]}" \
    -movflags +faststart "$5"
  echo "wrote $5"
}

enc 1080x1920 14 20M 4.2 "$out/${base}_INSTAGRAM_1080p.mp4" &
if [ "$want4k" = 1 ] && [ "$short" -ge 2160 ]; then
  enc 2160x3840 15 60M 5.2 "$out/${base}_INSTAGRAM_4K.mp4" &
elif [ "$want4k" = 1 ]; then
  echo "master is ${w}x${h}; skipping 4K file (would only be an upscale)"
fi
wait
