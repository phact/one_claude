# one_claude

TUI manager for Claude Code sessions - browse, search, and teleport across time.

## Features

- **Conversation Browser**: Navigate all your Claude Code conversations with a terminal interface. Branched sessions (from `/rewind`) display as a tree.
- **Search**: Filter conversations by title or message content
- **Teleport**: Resume any conversation in a tmux session with Claude on the left and a shell on the right

## Roadmap

- **Semantic Search**: Vector search across session content
- **P2P Sync**: Sync sessions across devices
- **S3 Backup**: Backup sessions to S3

## Usage

```bash
# Launch TUI
uvx one_claude

# List all sessions
uvx one_claude sessions

# Show a specific session
uvx one_claude show <session-id>

# Search sessions
uvx one_claude search "query" --mode text

# Extract thinking blocks (plans) from a session
uvx one_claude plans <session-id>
```

## Commands

### `plans` - Extract Thinking Blocks

Extract and display Claude's internal thinking blocks (plans) from a session in a readable format.

```bash
# Display thinking blocks in rich format (default)
uvx one_claude plans <session-id>

# Export to markdown
uvx one_claude plans <session-id> --format markdown --output plans.md

# Export to JSON
uvx one_claude plans <session-id> --format json --output plans.json

# Show with numbered blocks
uvx one_claude plans <session-id> --numbered
```

**Formats:**
- `rich` (default): Beautiful terminal output with panels and colors
- `markdown`: Formatted markdown with headers and sections
- `text`: Plain text format
- `json`: Structured JSON for programmatic access

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `j/k` | Navigate up/down |
| `gg` | Go to top |
| `G` | Go to bottom |
| `ctrl+u/d` | Half-page up/down |
| `ctrl+b/f` | Full-page up/down |
| `Enter` | Select/Open |
| `Esc` | Back / Clear search |
| `/` | Search |
| `t` | Teleport |
| `m` | Toggle execution mode (local/docker/microvm) |
| `y` | Copy conversation ID |
| `q` | Quit |
