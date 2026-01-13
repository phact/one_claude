# Tweet 5: "Teleport - local, docker, or microvm"
# Shows teleporting a session

Output "05_teleport.gif"
Set FontSize 16
Set Width 1400
Set Height 900
Set Theme "Dracula"
Set Padding 20

# Launch the TUI
Type "uvx one_claude"
Enter
Sleep 2s

# Navigate to a session
Type "j"
Sleep 300ms
Type "j"
Sleep 300ms
Type "j"
Sleep 300ms

# Show mode toggle
Type "m"
Sleep 1s
Type "m"
Sleep 1s
Type "m"
Sleep 1s

# Teleport!
Type "t"
Sleep 3s

# The tmux split should appear with Claude on left, shell on right
# Show some interaction
Sleep 2s
Enter
Sleep 2s
