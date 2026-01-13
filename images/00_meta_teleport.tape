# Tweet 0 or 7: Meta - "This thread was made with one_claude"
# Shows teleporting into THIS VERY CONVERSATION
# Use as opening hook or closing "inception" moment

Output "00_meta_teleport.gif"
Set FontSize 16
Set Width 1400
Set Height 900
Set Theme "Dracula"
Set Padding 20

# Launch
Type "uvx one_claude"
Enter
Sleep 2s

# Search for this conversation
Type "/"
Sleep 400ms
Type@80ms "tweet thread"
Sleep 1s
Enter
Sleep 1s

# It should find the session where we discussed the thread
# Navigate to it
Type "j"
Sleep 300ms

# Show it's this conversation
Enter
Sleep 2s

# Back out
Escape
Sleep 500ms

# Teleport into it
Type "t"
Sleep 3s

# Now we're in the session - Claude on left, shell on right
# The transcript shows our conversation about making this thread
Sleep 3s
