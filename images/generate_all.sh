#!/bin/bash
# Generate all images for the tweet thread
#
# Requirements:
#   - silicon: cargo install silicon
#   - vhs: brew install vhs (or go install github.com/charmbracelet/vhs@latest)
#   - ffmpeg: apt install ffmpeg / brew install ffmpeg
#
# Usage:
#   ./generate_all.sh          # Generate all
#   ./generate_all.sh silicon  # Only silicon/ffmpeg GIFs
#   ./generate_all.sh vhs      # Only VHS recordings

set -e
cd "$(dirname "$0")"

generate_silicon() {
    echo "=== Generating silicon + ffmpeg GIFs ==="

    for script in 01_too_many_claudes.sh 02_varnishing_context.sh 03_lost_rewinds.sh 07_meta_closing.sh; do
        echo "Running $script..."
        bash "$script"
    done
}

generate_vhs() {
    echo "=== Generating VHS recordings ==="

    for tape in 00_meta_teleport.tape 04_tui_tree.tape 05_teleport.tape 06_hero.tape; do
        echo "Recording $tape..."
        vhs "$tape"
    done
}

case "${1:-all}" in
    silicon)
        generate_silicon
        ;;
    vhs)
        generate_vhs
        ;;
    all)
        generate_silicon
        generate_vhs
        ;;
    *)
        echo "Usage: $0 [silicon|vhs|all]"
        exit 1
        ;;
esac

echo ""
echo "=== Done! Generated GIFs: ==="
ls -la *.gif 2>/dev/null || echo "No GIFs found"
