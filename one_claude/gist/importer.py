"""Import Claude sessions from GitHub Gist."""

import base64
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import orjson

from one_claude.gist.api import GistAPI


@dataclass
class ImportResult:
    """Result of an import operation."""

    success: bool
    session_id: str | None
    project_path: str | None
    error: str | None
    message_count: int
    checkpoint_count: int


def _escape_path(path: str) -> str:
    """Escape path for Claude's project directory naming.

    Claude escapes paths by replacing / with - for directory names.
    """
    # Remove leading slash and replace / with -
    escaped = path.lstrip("/").replace("/", "-")
    return escaped


def _parse_gist_id(url_or_id: str) -> str:
    """Extract gist ID from URL or return as-is."""
    url_or_id = url_or_id.strip()

    # URL format: https://gist.github.com/user/GIST_ID or https://gist.github.com/GIST_ID
    if "gist.github.com" in url_or_id:
        # Extract the last path segment
        match = re.search(r"gist\.github\.com/(?:[^/]+/)?([a-f0-9]+)", url_or_id)
        if match:
            return match.group(1)

    # Assume it's already a gist ID
    return url_or_id


def _is_base64(s: str) -> bool:
    """Check if string appears to be base64 encoded."""
    # Base64 strings are alphanumeric with + / and = padding
    if not s:
        return False
    # Check for binary indicators (null bytes would be encoded)
    try:
        decoded = base64.b64decode(s)
        # If it decodes and has null bytes, it was likely base64-encoded binary
        return b"\x00" in decoded[:1024]
    except Exception:
        return False


class SessionImporter:
    """Imports Claude sessions from GitHub gist."""

    def __init__(self, claude_dir: Path | None = None):
        self.claude_dir = claude_dir or Path.home() / ".claude"
        self.api = GistAPI()

    async def import_from_gist(self, gist_url_or_id: str) -> ImportResult:
        """Import session from gist URL or ID."""
        # Parse gist ID
        gist_id = _parse_gist_id(gist_url_or_id)

        # Fetch gist metadata
        gist_data, error = await self.api.get(gist_id)
        if error:
            return ImportResult(
                success=False,
                session_id=None,
                project_path=None,
                error=error,
                message_count=0,
                checkpoint_count=0,
            )

        # Find session.json
        files = gist_data.get("files", {})
        if "session.json" not in files:
            return ImportResult(
                success=False,
                session_id=None,
                project_path=None,
                error="No session.json in gist",
                message_count=0,
                checkpoint_count=0,
            )

        # Fetch session.json content
        session_file = files["session.json"]
        raw_url = session_file.get("raw_url")
        if not raw_url:
            return ImportResult(
                success=False,
                session_id=None,
                project_path=None,
                error="Cannot get session.json URL",
                message_count=0,
                checkpoint_count=0,
            )

        session_content, error = await self.api.get_raw_file(raw_url)
        if error:
            return ImportResult(
                success=False,
                session_id=None,
                project_path=None,
                error=f"Failed to fetch session.json: {error}",
                message_count=0,
                checkpoint_count=0,
            )

        # Parse export data
        try:
            export_data = orjson.loads(session_content)
        except Exception as e:
            return ImportResult(
                success=False,
                session_id=None,
                project_path=None,
                error=f"Invalid session.json: {e}",
                message_count=0,
                checkpoint_count=0,
            )

        # Generate new session ID to avoid collisions
        new_session_id = str(uuid.uuid4())

        # Get project path from export
        session_info = export_data.get("session", {})
        project_path = session_info.get("project_path", "/imported")

        # Create project directory
        project_dir_name = _escape_path(project_path)
        project_dir = self.claude_dir / "projects" / project_dir_name
        project_dir.mkdir(parents=True, exist_ok=True)

        # Reconstruct JSONL from messages
        messages = export_data.get("messages", [])
        jsonl_lines = []

        for msg_data in messages:
            # Update session ID in each message
            msg_data["sessionId"] = new_session_id
            jsonl_lines.append(orjson.dumps(msg_data).decode())

        # Write JSONL file
        jsonl_path = project_dir / f"{new_session_id}.jsonl"
        jsonl_path.write_text("\n".join(jsonl_lines) + "\n")

        # Restore checkpoints
        file_history_dir = self.claude_dir / "file-history" / new_session_id
        file_history_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_count = 0
        manifest = export_data.get("checkpoint_manifest", {})

        for path_hash, cp_info in manifest.items():
            versions = cp_info.get("versions", [])
            for version in versions:
                filename = f"checkpoint_{path_hash}@v{version}"
                if filename not in files:
                    continue

                # Fetch checkpoint content
                cp_file = files[filename]
                raw_url = cp_file.get("raw_url")
                if not raw_url:
                    continue

                content, error = await self.api.get_raw_file(raw_url)
                if error or content is None:
                    continue

                # Write checkpoint file
                checkpoint_path = file_history_dir / f"{path_hash}@v{version}"
                try:
                    # Check if content is base64 encoded (binary file)
                    if _is_base64(content):
                        checkpoint_path.write_bytes(base64.b64decode(content))
                    else:
                        checkpoint_path.write_text(content)
                    checkpoint_count += 1
                except Exception:
                    continue

        return ImportResult(
            success=True,
            session_id=new_session_id,
            project_path=project_path,
            error=None,
            message_count=len(messages),
            checkpoint_count=checkpoint_count,
        )
