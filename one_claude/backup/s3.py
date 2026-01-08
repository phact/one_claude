"""S3 backup functionality for one_claude."""

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any

# Try to import boto3
try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


class S3Backup:
    """S3 backup management for sessions."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "one_claude/",
        region: str | None = None,
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        self.region = region
        self._client = None

    @property
    def available(self) -> bool:
        """Check if boto3 is available."""
        return BOTO3_AVAILABLE

    @property
    def client(self):
        """Get or create S3 client."""
        if not BOTO3_AVAILABLE:
            raise RuntimeError("boto3 not installed. Install with: uv pip install boto3")

        if self._client is None:
            self._client = boto3.client("s3", region_name=self.region)

        return self._client

    def _session_key(self, session_id: str, project_path: str) -> str:
        """Generate S3 key for a session."""
        # Sanitize project path for use in key
        safe_project = project_path.replace("/", "_").strip("_")
        return f"{self.prefix}sessions/{safe_project}/{session_id}.jsonl.gz"

    def _file_history_key(self, session_id: str, path_hash: str, version: int) -> str:
        """Generate S3 key for a file checkpoint."""
        return f"{self.prefix}file-history/{session_id}/{path_hash}@v{version}.gz"

    async def upload_session(
        self,
        session_path: Path,
        session_id: str,
        project_path: str,
    ) -> bool:
        """Upload a session to S3."""
        try:
            # Read and compress
            content = session_path.read_bytes()
            compressed = gzip.compress(content)

            key = self._session_key(session_id, project_path)

            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=compressed,
                ContentType="application/gzip",
                Metadata={
                    "session_id": session_id,
                    "project_path": project_path,
                    "uploaded_at": datetime.now().isoformat(),
                    "original_size": str(len(content)),
                },
            )
            return True

        except ClientError as e:
            return False

    async def download_session(
        self,
        session_id: str,
        project_path: str,
        target_path: Path,
    ) -> bool:
        """Download a session from S3."""
        try:
            key = self._session_key(session_id, project_path)

            response = self.client.get_object(Bucket=self.bucket, Key=key)
            compressed = response["Body"].read()

            # Decompress and write
            content = gzip.decompress(compressed)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)

            return True

        except ClientError as e:
            return False

    async def upload_file_checkpoint(
        self,
        checkpoint_path: Path,
        session_id: str,
        path_hash: str,
        version: int,
    ) -> bool:
        """Upload a file checkpoint to S3."""
        try:
            content = checkpoint_path.read_bytes()
            compressed = gzip.compress(content)

            key = self._file_history_key(session_id, path_hash, version)

            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=compressed,
                ContentType="application/gzip",
                Metadata={
                    "session_id": session_id,
                    "path_hash": path_hash,
                    "version": str(version),
                    "original_size": str(len(content)),
                },
            )
            return True

        except ClientError as e:
            return False

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all backed up sessions."""
        sessions = []

        try:
            paginator = self.client.get_paginator("list_objects_v2")
            prefix = f"{self.prefix}sessions/"

            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith(".jsonl.gz"):
                        # Extract session info from key
                        parts = key[len(prefix) :].split("/")
                        if len(parts) >= 2:
                            sessions.append(
                                {
                                    "key": key,
                                    "project_path": parts[0],
                                    "session_id": parts[1].replace(".jsonl.gz", ""),
                                    "size": obj["Size"],
                                    "last_modified": obj["LastModified"].isoformat(),
                                }
                            )

        except ClientError:
            pass

        return sessions

    async def sync_to_s3(
        self,
        scanner,
        progress_callback=None,
    ) -> dict[str, int]:
        """Sync all sessions to S3."""
        results = {"uploaded": 0, "skipped": 0, "failed": 0}

        # Get list of already backed up sessions
        existing = await self.list_sessions()
        existing_ids = {s["session_id"] for s in existing}

        # Scan local sessions
        for project in scanner.scan_all():
            for session in project.sessions:
                if session.id in existing_ids:
                    results["skipped"] += 1
                    continue

                success = await self.upload_session(
                    session.jsonl_path,
                    session.id,
                    project.path,
                )

                if success:
                    results["uploaded"] += 1
                else:
                    results["failed"] += 1

                if progress_callback:
                    progress_callback(results)

        return results

    async def restore_from_s3(
        self,
        target_dir: Path,
        session_ids: list[str] | None = None,
    ) -> dict[str, int]:
        """Restore sessions from S3."""
        results = {"restored": 0, "failed": 0}

        sessions = await self.list_sessions()

        for session_info in sessions:
            if session_ids and session_info["session_id"] not in session_ids:
                continue

            project_path = session_info["project_path"]
            session_id = session_info["session_id"]

            target_path = target_dir / project_path / f"{session_id}.jsonl"

            success = await self.download_session(session_id, project_path, target_path)

            if success:
                results["restored"] += 1
            else:
                results["failed"] += 1

        return results


class MockS3Backup:
    """Mock S3 backup for testing."""

    def __init__(self, bucket: str, prefix: str = "one_claude/"):
        self.bucket = bucket
        self.prefix = prefix
        self._storage: dict[str, bytes] = {}

    @property
    def available(self) -> bool:
        return True

    async def upload_session(
        self,
        session_path: Path,
        session_id: str,
        project_path: str,
    ) -> bool:
        key = f"{self.prefix}sessions/{project_path}/{session_id}.jsonl.gz"
        self._storage[key] = gzip.compress(session_path.read_bytes())
        return True

    async def download_session(
        self,
        session_id: str,
        project_path: str,
        target_path: Path,
    ) -> bool:
        key = f"{self.prefix}sessions/{project_path}/{session_id}.jsonl.gz"
        if key in self._storage:
            content = gzip.decompress(self._storage[key])
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)
            return True
        return False

    async def list_sessions(self) -> list[dict]:
        return [
            {"key": k, "session_id": k.split("/")[-1].replace(".jsonl.gz", "")}
            for k in self._storage
            if k.endswith(".jsonl.gz")
        ]
