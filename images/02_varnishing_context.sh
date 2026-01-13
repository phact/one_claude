#!/bin/bash
# Tweet 2: "Code review gold varnishing away"
# Shows ~/.claude directory with valuable context being lost

set -e
cd "$(dirname "$0")"

FONT="Hack"
THEME="Dracula"
BG="#0d1117"
TARGET_W=1200
TARGET_H=700

# Frame 1: Disk usage
cat > /tmp/frame1.txt <<'EOF'
$ du -sh ~/.claude/
2.3G    /home/dev/.claude/

$ du -sh ~/.claude/projects/*/ | sort -hr | head -10
412M    ~/.claude/projects/api-gateway/
389M    ~/.claude/projects/frontend-v2/
256M    ~/.claude/projects/ml-inference/
198M    ~/.claude/projects/billing-service/
167M    ~/.claude/projects/one_claude/
145M    ~/.claude/projects/data-pipeline/
134M    ~/.claude/projects/user-service/
98M     ~/.claude/projects/mobile-app/
87M     ~/.claude/projects/search-indexer/
76M     ~/.claude/projects/internal-tools/
EOF

silicon /tmp/frame1.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_01_raw.png

# Frame 2: What's in there - rich context
cat > /tmp/frame2.txt <<'EOF'
$ ls ~/.claude/projects/api-gateway/
sessions/           # 47 conversations
file_history/       # checkpoint diffs
settings.json

# Each session contains:
#   - Full conversation transcript
#   - Tool calls and results
#   - File modifications with diffs
#   - The "why" behind every PR
#   - Debugging context that took hours
EOF

silicon /tmp/frame2.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_02_raw.png

# Frame 3: The loss
cat > /tmp/frame3.txt <<'EOF'
# Meanwhile, disk is getting full...

$ rm -rf ~/.claude/projects/old-client/
$ rm -rf ~/.claude/projects/legacy-api/
$ rm -rf ~/.claude/projects/2024-*

# All that context.
# All those code review insights.
# Gone.
EOF

silicon /tmp/frame3.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_03_raw.png

# Normalize all frames to target size
for i in 1 2 3; do
    ffmpeg -y -i "frame_0${i}_raw.png" \
        -vf "scale='min($TARGET_W,iw)':'min($TARGET_H,ih)':force_original_aspect_ratio=decrease,pad=$TARGET_W:$TARGET_H:(ow-iw)/2:(oh-ih)/2:color=$BG" \
        "frame_0${i}.png" 2>/dev/null
done

# Duplicate frames for timing
for i in 1 2 3; do
    for j in $(seq 1 15); do
        cp "frame_0${i}.png" "frame_0${i}_$(printf '%02d' $j).png"
    done
done

# Create GIF
ffmpeg -y -framerate 10 -pattern_type glob -i 'frame_0?_??.png' \
    -vf "split[s0][s1];[s0]palettegen=max_colors=256[p];[s1][p]paletteuse=dither=floyd_steinberg" \
    02_varnishing_context.gif 2>/dev/null

# Cleanup
rm -f frame_*.png /tmp/frame*.txt

echo "Created 02_varnishing_context.gif"
