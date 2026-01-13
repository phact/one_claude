# Tweet 4: "Git-log-graph-like UI"
# Shows the TUI with session tree view

Output "04_tui_tree.gif"
Set FontSize 16
Set Width 1400
Set Height 900
Set Theme "Dracula"
Set Padding 20

# Launch the TUI
Type "uvx one_claude"
Enter
Sleep 3s

# Navigate to show the tree structure
Type "j"
Sleep 400ms
Type "j"
Sleep 400ms
Type "j"
Sleep 400ms

# Expand a session to show branches
Enter
Sleep 1s

# Navigate through branches
Type "j"
Sleep 400ms
Type "j"
Sleep 400ms

# Go back
Escape
Sleep 500ms

# Show search
Type "/"
Sleep 500ms
Type@100ms "auth"
Sleep 1s
Enter
Sleep 1s

# Clear and show more
Escape
Sleep 500ms

# Navigate around
Type "G"
Sleep 1s
Type "gg"
Sleep 1s

# Quit
Type "q"
Sleep 500ms
