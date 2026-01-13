"""Export Claude sessions to GitHub Gist."""

import base64
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import orjson

from one_claude.core.file_history import FileHistoryManager, compute_path_hash
from one_claude.core.models import ConversationPath, Message, MessageType
from one_claude.core.parser import extract_file_paths_from_message
from one_claude.core.scanner import ClaudeScanner
from one_claude.gist.api import GistAPI

EXPORT_VERSION = "1.0"


@dataclass
class ExportResult:
    """Result of an export operation."""

    success: bool
    gist_url: str | None
    error: str | None
    message_count: int
    checkpoint_count: int


def get_git_info(cwd: str) -> dict | None:
    """Detect git repo info for the given directory."""
    try:

        def run(cmd: list[str]) -> str:
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else ""

        # Check if in git repo
        if not run(["git", "rev-parse", "--is-inside-work-tree"]):
            return None

        branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        commit = run(["git", "rev-parse", "HEAD"])

        # Get the remote that tracks this branch (more likely to have the commit)
        tracking_remote = ""
        if branch:
            tracking_remote = run(["git", "config", f"branch.{branch}.remote"])

        # Get remote URL - prefer tracking remote, fall back to origin
        remote = ""
        if tracking_remote:
            remote = run(["git", "remote", "get-url", tracking_remote])
        if not remote:
            remote = run(["git", "remote", "get-url", "origin"])

        # Also capture all remotes for reference
        all_remotes = {}
        remote_names = run(["git", "remote"]).split("\n")
        for name in remote_names:
            if name:
                url = run(["git", "remote", "get-url", name])
                if url:
                    all_remotes[name] = url

        return {
            "branch": branch or None,
            "commit": commit or None,
            "remote": remote or None,
            "all_remotes": all_remotes if all_remotes else None,
        }
    except Exception:
        return None


def _is_binary(data: bytes) -> bool:
    """Check if data appears to be binary."""
    # Check for null bytes in first 8KB
    return b"\x00" in data[:8192]


def _make_import_script(gist_id: str) -> str:
    """Generate shell script for curl | bash."""
    return f'''#!/bin/bash
# Import this Claude session: curl -sL <raw_url> | bash

if ! command -v uvx &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

uvx one_claude gist import {gist_id}
'''


def _serialize_message(msg: Message) -> dict:
    """Serialize a message for export."""
    data = {
        "uuid": msg.uuid,
        "parentUuid": msg.parent_uuid,
        "type": msg.type.value,
        "timestamp": msg.timestamp.isoformat(),
        "sessionId": msg.session_id,
        "cwd": msg.cwd,
    }

    if msg.text_content:
        data["text"] = msg.text_content

    if msg.git_branch:
        data["gitBranch"] = msg.git_branch

    if msg.version:
        data["version"] = msg.version

    if msg.is_sidechain:
        data["isSidechain"] = True

    if msg.user_type:
        data["userType"] = msg.user_type.value

    if msg.model:
        data["model"] = msg.model

    if msg.request_id:
        data["requestId"] = msg.request_id

    if msg.thinking:
        data["thinking"] = {
            "content": msg.thinking.content,
            "signature": msg.thinking.signature,
        }

    if msg.tool_uses:
        data["toolUses"] = [
            {"id": tu.id, "name": tu.name, "input": tu.input} for tu in msg.tool_uses
        ]

    if msg.tool_result:
        data["toolResult"] = {
            "toolUseId": msg.tool_result.tool_use_id,
            "content": msg.tool_result.content,
            "isError": msg.tool_result.is_error,
        }

    if msg.summary_text:
        data["summary"] = msg.summary_text

    if msg.snapshot_data:
        data["snapshot"] = msg.snapshot_data

    if msg.system_subtype:
        data["systemSubtype"] = msg.system_subtype

    if msg.system_data:
        data["systemData"] = msg.system_data

    return data


class SessionExporter:
    """Exports Claude sessions to GitHub gist."""

    def __init__(self, scanner: ClaudeScanner):
        self.scanner = scanner
        self.file_history = FileHistoryManager(scanner.file_history_dir)
        self.api = GistAPI()

    async def export_full_session(self, path: ConversationPath) -> ExportResult:
        """Export entire session from conversation path."""
        return await self._export(path, from_message_uuid=None)

    async def export_from_message(
        self,
        path: ConversationPath,
        message_uuid: str,
    ) -> ExportResult:
        """Export session from specific message onward."""
        return await self._export(path, from_message_uuid=message_uuid)

    async def _export(
        self,
        path: ConversationPath,
        from_message_uuid: str | None,
    ) -> ExportResult:
        """Execute the export."""
        if not path.jsonl_files:
            return ExportResult(
                success=False,
                gist_url=None,
                error="No JSONL files to export",
                message_count=0,
                checkpoint_count=0,
            )

        # Read raw JSONL lines (preserves Claude's native format)
        jsonl_file = path.jsonl_files[-1]
        raw_messages = []
        with open(jsonl_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = orjson.loads(line)
                    raw_messages.append(msg)
                except Exception:
                    continue

        if not raw_messages:
            return ExportResult(
                success=False,
                gist_url=None,
                error="No messages to export",
                message_count=0,
                checkpoint_count=0,
            )

        # Filter if from_message specified
        if from_message_uuid:
            idx = next(
                (i for i, m in enumerate(raw_messages) if m.get("uuid") == from_message_uuid),
                0,
            )
            raw_messages = raw_messages[idx:]

        # Get git info from project path
        git_info = None
        if path.project_display and Path(path.project_display).exists():
            git_info = get_git_info(path.project_display)

        # Get session ID and checkpoints
        session_id = jsonl_file.stem
        checkpoints = self.file_history.get_checkpoints_for_session(session_id)

        # Load parsed messages for path mapping (still needed for checkpoint paths)
        messages = self.scanner.load_conversation_path_messages(path)
        path_mapping = self._build_path_mapping(messages)

        # Build checkpoint manifest
        checkpoint_manifest = {}
        for path_hash, versions in checkpoints.items():
            original_path = path_mapping.get(path_hash)
            checkpoint_manifest[path_hash] = {
                "original_path": original_path,
                "versions": [cp.version for cp in versions],
            }

        # Build export data with raw messages (native Claude format)
        export_data = {
            "version": EXPORT_VERSION,
            "export_type": "from_message" if from_message_uuid else "full",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "exporter": "one_claude",
            "session": {
                "id": session_id,
                "project_path": path.project_display,
                "title": path.title or "Untitled",
                "created_at": path.created_at.isoformat(),
                "updated_at": path.updated_at.isoformat(),
            },
            "git_info": git_info,
            "messages": raw_messages,  # Native Claude format
            "checkpoint_manifest": checkpoint_manifest,
            "from_message_uuid": from_message_uuid,
        }

        # Prepare gist files
        gist_files = {
            "session.json": orjson.dumps(export_data, option=orjson.OPT_INDENT_2).decode()
        }

        # Add checkpoint files
        checkpoint_count = 0
        for path_hash, versions in checkpoints.items():
            for cp in versions:
                try:
                    content = cp.read_content()
                    filename = f"checkpoint_{path_hash}@v{cp.version}"
                    if _is_binary(content):
                        # Base64 encode binary files
                        gist_files[filename] = base64.b64encode(content).decode()
                    else:
                        gist_files[filename] = content.decode("utf-8", errors="replace")
                    checkpoint_count += 1
                except Exception:
                    continue

        # Add import script (will be updated with actual gist ID after creation)
        gist_files["import.sh"] = _make_import_script("GIST_ID_PLACEHOLDER")

        # Create gist
        description = f"Claude session: {session_id}"
        gist_url, error = await self.api.create(gist_files, description)

        # Track export and update import script with real gist ID
        if gist_url:
            from one_claude.gist.store import add_export

            gist_id = gist_url.rstrip("/").split("/")[-1]

            # Update import script with real gist ID
            gist_files["import.sh"] = _make_import_script(gist_id)
            await self.api.update(gist_id, {"import.sh": gist_files["import.sh"]})

            add_export(
                gist_url=gist_url,
                session_id=session_id,
                title=path.title or "Untitled",
                message_count=len(messages),
                checkpoint_count=checkpoint_count,
            )

        return ExportResult(
            success=gist_url is not None,
            gist_url=gist_url,
            error=error,
            message_count=len(messages),
            checkpoint_count=checkpoint_count,
        )

    def _build_path_mapping(self, messages: list[Message]) -> dict[str, str]:
        """Build mapping from path hash to original path."""
        mapping: dict[str, str] = {}
        for msg in messages:
            paths = extract_file_paths_from_message(msg)
            for file_path in paths:
                path_hash = compute_path_hash(file_path)
                if path_hash not in mapping:
                    mapping[path_hash] = file_path
        return mapping
