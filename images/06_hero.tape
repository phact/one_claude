# Tweet 6: "One place - the hero shot"
# Full demo of one_claude

Output "06_hero.gif"
Set FontSize 16
Set Width 1400
Set Height 900
Set Theme "Dracula"
Set Padding 20

# Start with the command
Type "# one place to manage all your claudes"
Enter
Sleep 1s
Type "uvx one_claude"
Enter
Sleep 3s

# Quick navigation demo
Type "j"
Sleep 200ms
Type "j"
Sleep 200ms
Type "j"
Sleep 200ms
Type "k"
Sleep 200ms
Type "k"
Sleep 200ms

# Page navigation
Ctrl+d
Sleep 500ms
Ctrl+u
Sleep 500ms

# Search
Type "/"
Sleep 400ms
Type@80ms "refactor"
Sleep 800ms
Enter
Sleep 1s

# Clear search
Escape
Sleep 500ms

# Copy session ID
Type "y"
Sleep 1s

# Show mode options
Type "m"
Sleep 800ms
Type "m"
Sleep 800ms

# Jump to top
Type "gg"
Sleep 500ms

# Select and view
Enter
Sleep 1s

# Back
Escape
Sleep 500ms

# Teleport teaser
Type "t"
Sleep 2s

# End
Type "q"
Sleep 500ms
