# one_claude

TUI manager for Claude Code sessions - browse, search, and teleport across time.

## Features

- **TUI Browser**: Navigate all your Claude Code sessions with a terminal interface
- **Search**: Text search across session content
- **Teleport**: Restore file state from any point in session history using microsandbox


## Roadmap:

- **P2P Sync**: Sync sessions across devices (coming soon)
- **S3 Backup**: Backup sessions to S3 (coming soon)

## Usage 

```bash
# Launch TUI
uvx one_claude
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `j/k` | Navigate up/down |
| `Enter` | Select/Open |
| `Esc` | Back |
| `/` | Search |
| `t` | Teleport |
| `m` | Toggle execution mode |
| `q` | Quit |
