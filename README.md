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
```

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
