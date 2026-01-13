#!/bin/bash
# Tweet 1: "Too many Claudes running around"
# Uses silicon to create frames, ffmpeg to animate

set -e
cd "$(dirname "$0")"

FONT="Hack"
THEME="Dracula"
BG="#0d1117"
# Target size (will pad smaller frames to this)
TARGET_W=1200
TARGET_H=700

# Frame 1: tmux ls showing many sessions
cat > /tmp/frame1.txt <<'EOF'
$ tmux ls
api-refactor: 3 windows (created Mon Jan  6 09:14:22 2025)
auth-fix: 2 windows (created Mon Jan  6 11:32:45 2025)
bug-1847: 1 windows (created Tue Jan  7 14:22:11 2025)
claude-review: 2 windows (created Tue Jan  7 16:45:33 2025)
feature-export: 4 windows (created Wed Jan  8 08:12:19 2025)
hotfix-prod: 1 windows (created Wed Jan  8 22:01:44 2025)
main-dev: 5 windows (created Thu Jan  9 07:30:00 2025)
perf-testing: 2 windows (created Thu Jan  9 15:18:27 2025)
pr-review-423: 1 windows (created Fri Jan 10 10:45:12 2025)
EOF

silicon /tmp/frame1.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_01_raw.png

# Frame 2: Multiple projects in ~/.claude
cat > /tmp/frame2.txt <<'EOF'
$ ls ~/.claude/projects/ | head -15
api-gateway/
billing-service/
claude-mcp-server/
data-pipeline/
frontend-v2/
internal-tools/
ml-inference/
mobile-app/
one_claude/
payment-processor/
search-indexer/
user-service/
... and 23 more
EOF

silicon /tmp/frame2.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_02_raw.png

# Frame 3: The existential question
cat > /tmp/frame3.txt <<'EOF'
$ # which terminal had my auth fix?
$ # was it the laptop or the desktop?
$ # did I /rewind that session?
$ # what was the session id again?
$ claude -r ???
EOF

silicon /tmp/frame3.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_03_raw.png

# Normalize all frames to target size using ffmpeg (pad with bg color, centered)
for i in 1 2 3; do
    ffmpeg -y -i "frame_0${i}_raw.png" \
        -vf "scale='min($TARGET_W,iw)':'min($TARGET_H,ih)':force_original_aspect_ratio=decrease,pad=$TARGET_W:$TARGET_H:(ow-iw)/2:(oh-ih)/2:color=$BG" \
        "frame_0${i}.png" 2>/dev/null
done

# Duplicate frames for timing (1.5s per frame at 10fps = 15 copies)
for i in 1 2 3; do
    for j in $(seq 1 15); do
        cp "frame_0${i}.png" "frame_0${i}_$(printf '%02d' $j).png"
    done
done

# Create GIF with palette optimization
ffmpeg -y -framerate 10 -pattern_type glob -i 'frame_0?_??.png' \
    -vf "split[s0][s1];[s0]palettegen=max_colors=256[p];[s1][p]paletteuse=dither=floyd_steinberg" \
    01_too_many_claudes.gif 2>/dev/null

# Cleanup
rm -f frame_*.png /tmp/frame*.txt

echo "Created 01_too_many_claudes.gif"
