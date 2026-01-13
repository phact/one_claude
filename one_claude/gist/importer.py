"""Import Claude sessions from GitHub Gist."""

import base64
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import orjson

from one_claude.core import escape_project_path
from one_claude.gist.api import GistAPI

# Namespace for generating deterministic session IDs from gist IDs
GIST_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def gist_to_session_id(gist_id: str) -> str:
    """Generate a deterministic session ID from a gist ID."""
    return str(uuid.uuid5(GIST_NAMESPACE, gist_id))


@dataclass
class ImportResult:
    """Result of an import operation."""

    success: bool
    session_id: str | None
    project_path: str | None
    error: str | None
    message_count: int
    checkpoint_count: int
    files_restored: int = 0
    already_imported: bool = False


@dataclass
class ExportInfo:
    """Metadata about an export before importing."""

    gist_id: str
    session_id: str  # Deterministic ID we'll use
    original_session_id: str | None
    project_path: str
    git_info: dict | None  # {remote, branch, commit}
    project_exists: bool
    message_count: int
    export_data: dict  # Raw export data for import


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
        self._gist_files: dict = {}  # Cache for gist files

    async def fetch_export_info(self, gist_url_or_id: str) -> ExportInfo | str:
        """Fetch export metadata without importing.

        Returns ExportInfo on success, or error string on failure.
        """
        gist_id = _parse_gist_id(gist_url_or_id)

        # Fetch gist metadata
        gist_data, error = await self.api.get(gist_id)
        if error:
            return error

        files = gist_data.get("files", {})
        if "session.json" not in files:
            return "No session.json in gist"

        # Fetch session.json content
        session_file = files["session.json"]
        raw_url = session_file.get("raw_url")
        if not raw_url:
            return "Cannot get session.json URL"

        session_content, error = await self.api.get_raw_file(raw_url)
        if error:
            return f"Failed to fetch session.json: {error}"

        try:
            export_data = orjson.loads(session_content)
        except Exception as e:
            return f"Invalid session.json: {e}"

        # Cache gist files for later import
        self._gist_files = files

        session_info = export_data.get("session", {})
        project_path = session_info.get("project_path", "/imported")
        git_info = export_data.get("git_info")
        messages = export_data.get("messages", [])

        return ExportInfo(
            gist_id=gist_id,
            session_id=gist_to_session_id(gist_id),
            original_session_id=session_info.get("id"),
            project_path=project_path,
            git_info=git_info,
            project_exists=Path(project_path).exists(),
            message_count=len(messages),
            export_data=export_data,
        )

    async def import_session(
        self,
        info: ExportInfo,
        project_path: str | None = None,
        restore_files: bool = False,
    ) -> ImportResult:
        """Import session using previously fetched ExportInfo.

        Args:
            info: ExportInfo from fetch_export_info()
            project_path: Override project path (e.g., after cloning repo)
            restore_files: If True, restore checkpoint files to project directory
        """
        # Use override path or original
        final_project_path = project_path or info.project_path
        session_id = info.session_id

        # Create project directory in Claude's config
        project_dir_name = escape_project_path(final_project_path)
        project_dir = self.claude_dir / "projects" / project_dir_name
        project_dir.mkdir(parents=True, exist_ok=True)

        # Check if already imported
        jsonl_path = project_dir / f"{session_id}.jsonl"
        if jsonl_path.exists():
            return ImportResult(
                success=True,
                session_id=session_id,
                project_path=final_project_path,
                error=None,
                message_count=0,
                checkpoint_count=0,
                already_imported=True,
            )

        # Write messages (update session ID and cwd if path changed)
        messages = info.export_data.get("messages", [])
        jsonl_lines = []

        for msg_data in messages:
            msg_data["sessionId"] = session_id
            # Update cwd if project path changed
            if project_path and msg_data.get("cwd") == info.project_path:
                msg_data["cwd"] = final_project_path
            jsonl_lines.append(orjson.dumps(msg_data).decode())

        jsonl_path.write_text("\n".join(jsonl_lines) + "\n")

        # Restore checkpoints to file-history (and optionally project directory)
        file_history_dir = self.claude_dir / "file-history" / session_id
        file_history_dir.mkdir(parents=True, exist_ok=True)

        project_dir_path = Path(final_project_path)

        checkpoint_count = 0
        files_restored = 0
        manifest = info.export_data.get("checkpoint_manifest", {})
        files = self._gist_files

        # Track latest version for each file to restore to project
        latest_contents: dict[str, tuple[str, bytes]] = {}  # path_hash -> (original_path, content)

        for path_hash, cp_info in manifest.items():
            versions = cp_info.get("versions", [])
            original_path = cp_info.get("original_path")

            for version in versions:
                filename = f"checkpoint_{path_hash}@v{version}"
                if filename not in files:
                    continue

                cp_file = files[filename]
                raw_url = cp_file.get("raw_url")
                if not raw_url:
                    continue

                content, error = await self.api.get_raw_file(raw_url)
                if error or content is None:
                    continue

                # Decode if base64
                if _is_base64(content):
                    content_bytes = base64.b64decode(content)
                else:
                    content_bytes = content.encode() if isinstance(content, str) else content

                # Save to file-history
                checkpoint_path = file_history_dir / f"{path_hash}@v{version}"
                try:
                    checkpoint_path.write_bytes(content_bytes)
                    checkpoint_count += 1

                    # Track latest version for project restore
                    if original_path:
                        latest_contents[path_hash] = (original_path, content_bytes)
                except Exception:
                    continue

        # Restore latest checkpoint files to project directory (if requested)
        if restore_files and latest_contents:
            project_dir_path.mkdir(parents=True, exist_ok=True)

            for path_hash, (original_path, content_bytes) in latest_contents.items():
                # Adjust path if project location changed
                if project_path and original_path.startswith(info.project_path):
                    relative = original_path[len(info.project_path):].lstrip("/")
                    dest_path = project_dir_path / relative
                elif original_path.startswith("/"):
                    # Absolute path - make relative to project
                    dest_path = project_dir_path / Path(original_path).name
                else:
                    dest_path = project_dir_path / original_path

                try:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_bytes(content_bytes)
                    files_restored += 1
                except Exception:
                    continue

        return ImportResult(
            success=True,
            session_id=session_id,
            project_path=final_project_path,
            error=None,
            message_count=len(messages),
            checkpoint_count=checkpoint_count,
            files_restored=files_restored,
        )

    async def import_from_gist(
        self,
        gist_url_or_id: str,
        project_path: str | None = None,
    ) -> ImportResult:
        """Import session from gist URL or ID (convenience method)."""
        info = await self.fetch_export_info(gist_url_or_id)
        if isinstance(info, str):
            return ImportResult(
                success=False,
                session_id=None,
                project_path=None,
                error=info,
                message_count=0,
                checkpoint_count=0,
            )
        return await self.import_session(info, project_path)
