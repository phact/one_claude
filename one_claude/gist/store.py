"""Store for tracking exported gists."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import orjson

EXPORTS_FILE = Path.home() / ".one_claude" / "exports.json"


@dataclass
class ExportRecord:
    """Record of an exported gist."""

    gist_url: str
    gist_id: str
    session_id: str
    title: str
    exported_at: str
    message_count: int
    checkpoint_count: int


def load_exports() -> list[ExportRecord]:
    """Load export records from disk."""
    if not EXPORTS_FILE.exists():
        return []
    try:
        data = orjson.loads(EXPORTS_FILE.read_bytes())
        return [ExportRecord(**r) for r in data]
    except Exception:
        return []


def save_exports(records: list[ExportRecord]) -> None:
    """Save export records to disk."""
    EXPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "gist_url": r.gist_url,
            "gist_id": r.gist_id,
            "session_id": r.session_id,
            "title": r.title,
            "exported_at": r.exported_at,
            "message_count": r.message_count,
            "checkpoint_count": r.checkpoint_count,
        }
        for r in records
    ]
    EXPORTS_FILE.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))


def add_export(
    gist_url: str,
    session_id: str,
    title: str,
    message_count: int,
    checkpoint_count: int,
) -> None:
    """Add a new export record."""
    # Extract gist ID from URL
    gist_id = gist_url.rstrip("/").split("/")[-1]

    record = ExportRecord(
        gist_url=gist_url,
        gist_id=gist_id,
        session_id=session_id,
        title=title,
        exported_at=datetime.now().isoformat(),
        message_count=message_count,
        checkpoint_count=checkpoint_count,
    )

    records = load_exports()
    # Remove duplicate if exists
    records = [r for r in records if r.gist_id != gist_id]
    records.insert(0, record)  # Add at start (newest first)
    save_exports(records)


def delete_export(gist_id: str) -> None:
    """Delete an export record."""
    records = load_exports()
    records = [r for r in records if r.gist_id != gist_id]
    save_exports(records)
