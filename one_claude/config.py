"""Configuration management for one_claude."""

from dataclasses import dataclass, field
from pathlib import Path

import orjson


@dataclass
class TUIConfig:
    """TUI-specific configuration."""

    theme: str = "dark"
    show_thinking_blocks: bool = False
    max_message_preview_length: int = 200
    date_format: str = "relative"  # "relative", "absolute", "iso"


@dataclass
class Config:
    """Main configuration for one_claude."""

    # Paths
    claude_dir: Path = field(default_factory=lambda: Path.home() / ".claude")
    data_dir: Path = field(default_factory=lambda: Path.home() / ".one_claude")

    # Indexing
    auto_index: bool = True
    embedding_model: str = "text-embedding-3-small"

    # TUI
    tui: TUIConfig = field(default_factory=TUIConfig)

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Config":
        """Load configuration from file."""
        if config_path is None:
            config_path = Path.home() / ".one_claude" / "config.json"

        if not config_path.exists():
            return cls()

        try:
            with open(config_path, "rb") as f:
                data = orjson.loads(f.read())
            return cls.from_dict(data)
        except Exception:
            return cls()

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Create config from dictionary."""
        config = cls()

        if "claude_dir" in data:
            config.claude_dir = Path(data["claude_dir"]).expanduser()
        if "data_dir" in data:
            config.data_dir = Path(data["data_dir"]).expanduser()
        if "auto_index" in data:
            config.auto_index = data["auto_index"]
        if "embedding_model" in data:
            config.embedding_model = data["embedding_model"]

        if "tui" in data:
            tui_data = data["tui"]
            config.tui = TUIConfig(
                theme=tui_data.get("theme", "dark"),
                show_thinking_blocks=tui_data.get("show_thinking_blocks", False),
                max_message_preview_length=tui_data.get("max_message_preview_length", 200),
                date_format=tui_data.get("date_format", "relative"),
            )

        return config

    def save(self, config_path: Path | None = None) -> None:
        """Save configuration to file."""
        if config_path is None:
            config_path = self.data_dir / "config.json"

        config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "claude_dir": str(self.claude_dir),
            "data_dir": str(self.data_dir),
            "auto_index": self.auto_index,
            "embedding_model": self.embedding_model,
            "tui": {
                "theme": self.tui.theme,
                "show_thinking_blocks": self.tui.show_thinking_blocks,
                "max_message_preview_length": self.tui.max_message_preview_length,
                "date_format": self.tui.date_format,
            },
        }

        with open(config_path, "wb") as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
