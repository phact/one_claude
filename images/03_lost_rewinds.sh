#!/bin/bash
# Tweet 3: "esc esc rewind - lost history"
# Shows the pain of losing rewound sessions

set -e
cd "$(dirname "$0")"

FONT="Hack"
THEME="Dracula"
BG="#0d1117"
TARGET_W=1200
TARGET_H=700

# Frame 1: The rewind moment
cat > /tmp/frame1.txt <<'EOF'
# In the middle of debugging auth...

Claude: I've identified the issue. The JWT validation
        is failing because the token expiry check uses
        UTC but your server is configured for local time.

        Let me also refactor the middleware to--

> *esc esc*  # oops, running low on context

/rewind
Rewinding to message 23 of 47...
EOF

silicon /tmp/frame1.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_01_raw.png

# Frame 2: Later, needing that context
cat > /tmp/frame2.txt <<'EOF'
# 3 days later, similar bug appears...

$ # What was that timezone thing Claude explained?
$ # Which session was it?
$ # What was the fix?

$ ls ~/.claude/projects/api-gateway/sessions/
01JFGH...  01JFGI...  01JFGJ...  01JFGK...
01JFGL...  01JFGM...  01JFGN...  01JFGO...
01JFGP...  01JFGQ...  01JFGR...  01JFGS...
# ... 47 ULIDs staring back at you
EOF

silicon /tmp/frame2.txt --language bash --font "$FONT" --theme "$THEME" \
    --background "$BG" --pad-horiz 40 --pad-vert 40 \
    --output frame_02_raw.png

# Frame 3: The futile search
cat > /tmp/frame3.txt <<'EOF'
$ # Fine, I'll grep for it

$ grep -r "timezone" ~/.claude/projects/api-gateway/
# 2,847 matches across 31 sessions

$ # Maybe just try session IDs?

$ claude -r 01JFGK...  # wrong one
$ claude -r 01JFGL...  # also wrong
$ claude -r 01JFGM...  # nope

$ # I'll just figure it out again...
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
    03_lost_rewinds.gif 2>/dev/null

# Cleanup
rm -f frame_*.png /tmp/frame*.txt

echo "Created 03_lost_rewinds.gif"
