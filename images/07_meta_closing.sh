#!/bin/bash
# Tweet 7: Meta closing - "This thread was made in a session you can teleport to"

set -e
cd "$(dirname "$0")"

FONT="Hack"
THEME="Dracula"
BG="#0d1117"
TARGET_W=1200
TARGET_H=500

# Frame 1: The reveal
cat > /tmp/frame1.txt <<'EOF'
# This tweet thread was written in a Claude session

# You can teleport into it right now:

$ uvx one_claude gist import 442c90987ba7736c4482464485209730
EOF

silicon /tmp/frame1.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_01_raw.png

# Frame 2: What you get
cat > /tmp/frame2.txt <<'EOF'
Importing session from gist...
[ok] Downloaded transcript (847 messages)
[ok] Restored file checkpoints
[ok] Rebuilt conversation tree

Session ready. Teleporting...

# tmux splits: Claude resumes left, shell right
# Full context preserved. Continue where I left off.
EOF

silicon /tmp/frame2.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_02_raw.png

# Normalize all frames to target size
for i in 1 2; do
    ffmpeg -y -i "frame_0${i}_raw.png" \
        -vf "scale='min($TARGET_W,iw)':'min($TARGET_H,ih)':force_original_aspect_ratio=decrease,pad=$TARGET_W:$TARGET_H:(ow-iw)/2:(oh-ih)/2:color=$BG" \
        "frame_0${i}.png" 2>/dev/null
done

# Duplicate frames for timing (2s per frame at 10fps = 20 copies)
for i in 1 2; do
    for j in $(seq 1 20); do
        cp "frame_0${i}.png" "frame_0${i}_$(printf '%02d' $j).png"
    done
done

# Create GIF
ffmpeg -y -framerate 10 -pattern_type glob -i 'frame_0?_??.png' \
    -vf "split[s0][s1];[s0]palettegen=max_colors=256[p];[s1][p]paletteuse=dither=floyd_steinberg" \
    07_meta_closing.gif 2>/dev/null

# Cleanup
rm -f frame_*.png /tmp/frame*.txt

echo "Created 07_meta_closing.gif"
