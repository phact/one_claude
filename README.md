# one_claude

Atuin-like tool for Claude Code sessions - browse, search, and teleport across time.

## Features

- **TUI Browser**: Navigate all your Claude Code sessions with a terminal interface
- **Search**: Text and semantic search across session content
- **Teleport**: Restore file state from any point in session history using microsandbox
- **P2P Sync**: Sync sessions across devices (coming soon)
- **S3 Backup**: Backup sessions to S3 (coming soon)

## Installation

```bash
uv pip install -e .
```

## Usage

```bash
# Launch TUI
one_claude

# Or run as module
python -m one_claude

# List sessions
one_claude sessions

# List projects
one_claude projects

# Show a session
one_claude show <session-id>
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `j/k` | Navigate up/down |
| `Enter` | Select/Open |
| `Esc` | Back |
| `/` | Search |
| `t` | Teleport |
| `q` | Quit |
