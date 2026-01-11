# Changelog

## [0.1.2]

### Added
- **Conversation paths**: Sessions now display as linear conversation paths. Branched conversations (from `/rewind`) show with tree visualization (├─, └─, │)
- **Branch indicators**: Conversations with multiple branches show branch count
- **Last message preview**: Each conversation shows truncated last user message for context
- **Vim navigation**: `gg` (top), `G` (bottom), `ctrl+u/d` (half-page), `ctrl+b/f` (full-page)
- **Tmux teleport**: Teleport opens tmux with dual panes - Claude on left, shell on right
- **Better clipboard**: Tries `wl-copy` (Wayland), `xclip` (X11), `pbcopy` (macOS) before falling back to pyperclip

### Changed
- Home screen renamed from "Sessions" to "Conversations"
- Search integrated into home screen (no separate search screen)
- Search now highlights matches while dimming non-matches to preserve tree context
- Teleport uses `--resume <id>` instead of `--continue`
- Switched JSON parser from orjson to simdjson for faster session loading

### Fixed
- Path unescaping for hidden directories (`.local`, `.config`, etc.)
- Sessions with only summary messages no longer appear in list

## [0.1.1] - 2025-01-10

### Added
- Initial release
- TUI browser for Claude Code sessions
- Text search across sessions
- Teleport to restore file state (local, docker, microvm modes)
