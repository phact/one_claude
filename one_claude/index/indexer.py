"""Session indexer for one_claude.

Handles indexing sessions for search, including:
- Building text indices
- Generating embeddings for semantic search
- Watching for new/modified sessions
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from one_claude.core.models import Session
from one_claude.core.scanner import ClaudeScanner
from one_claude.index.embeddings import EmbeddingGenerator
from one_claude.index.vector_store import FallbackVectorStore, VectorStore

# Try to import watchfiles
try:
    from watchfiles import awatch, Change

    WATCHFILES_AVAILABLE = True
except ImportError:
    WATCHFILES_AVAILABLE = False


@dataclass
class IndexStats:
    """Statistics about the index."""

    total_sessions: int
    indexed_sessions: int
    last_indexed: datetime | None
    index_size_bytes: int


class SessionIndexer:
    """Indexes sessions for fast searching."""

    def __init__(self, scanner: ClaudeScanner, data_dir: Path | None = None):
        self.scanner = scanner
        self.data_dir = data_dir or Path.home() / ".one_claude"
        self.index_dir = self.data_dir / "index"

        self._embedder: EmbeddingGenerator | None = None
        self._vector_store: VectorStore | FallbackVectorStore | None = None
        self._indexed_sessions: set[str] = set()
        self._watching = False

    def _get_embedder(self) -> EmbeddingGenerator:
        """Get or create embedder."""
        if self._embedder is None:
            cache_dir = self.data_dir / "cache" / "embeddings"
            self._embedder = EmbeddingGenerator(cache_dir=cache_dir)
        return self._embedder

    def _get_vector_store(self) -> VectorStore | FallbackVectorStore:
        """Get or create vector store."""
        if self._vector_store is None:
            store_dir = self.index_dir / "vectors"
            try:
                self._vector_store = VectorStore(store_dir)
                if not self._vector_store.available:
                    self._vector_store = FallbackVectorStore(store_dir)
            except Exception:
                self._vector_store = FallbackVectorStore(store_dir)
        return self._vector_store

    def index_session(self, session: Session) -> bool:
        """Index a single session."""
        embedder = self._get_embedder()
        if not embedder.available:
            return False

        try:
            embedding = embedder.embed_session(session, self.scanner)
            vector_store = self._get_vector_store()
            vector_store.add(session.id, embedding)
            self._indexed_sessions.add(session.id)
            return True
        except Exception:
            return False

    def index_all(
        self,
        force: bool = False,
        progress_callback: Callable[[int, int, int], None] | None = None,
    ) -> int:
        """Index all sessions.

        Args:
            force: Re-index even if already indexed
            progress_callback: Called with (current, total, indexed) counts
        """
        embedder = self._get_embedder()
        if not embedder.available:
            return 0

        sessions = self.scanner.get_sessions_flat()
        vector_store = self._get_vector_store()

        indexed = 0
        for i, session in enumerate(sessions):
            if not force and session.id in self._indexed_sessions:
                continue

            try:
                embedding = embedder.embed_session(session, self.scanner)
                vector_store.add(session.id, embedding)
                self._indexed_sessions.add(session.id)
                indexed += 1
            except Exception:
                pass

            if progress_callback:
                progress_callback(i + 1, len(sessions), indexed)

        vector_store.save()
        return indexed

    def get_stats(self) -> IndexStats:
        """Get index statistics."""
        sessions = self.scanner.get_sessions_flat()
        vector_store = self._get_vector_store()

        # Calculate index size
        index_size = 0
        if self.index_dir.exists():
            for f in self.index_dir.rglob("*"):
                if f.is_file():
                    index_size += f.stat().st_size

        return IndexStats(
            total_sessions=len(sessions),
            indexed_sessions=vector_store.size,
            last_indexed=None,  # Could track this
            index_size_bytes=index_size,
        )

    async def watch(
        self,
        callback: Callable[[Session, str], None] | None = None,
    ) -> None:
        """Watch for new/modified sessions and index them.

        Args:
            callback: Called with (session, change_type) when changes detected
        """
        if not WATCHFILES_AVAILABLE:
            raise RuntimeError(
                "watchfiles not installed. Install with: uv pip install watchfiles"
            )

        self._watching = True
        projects_dir = self.scanner.claude_dir / "projects"

        async for changes in awatch(projects_dir):
            if not self._watching:
                break

            for change_type, path in changes:
                path = Path(path)

                # Only care about JSONL files
                if not path.suffix == ".jsonl":
                    continue

                # Parse session info from path
                try:
                    session_id = path.stem
                    project_path = path.parent.name

                    # Find or create session
                    session = self._find_session(session_id)
                    if session:
                        change_str = "modified" if change_type == Change.modified else "added"

                        # Re-index the session
                        self.index_session(session)

                        if callback:
                            callback(session, change_str)
                except Exception:
                    pass

    def stop_watching(self) -> None:
        """Stop watching for changes."""
        self._watching = False

    def _find_session(self, session_id: str) -> Session | None:
        """Find a session by ID."""
        for project in self.scanner.scan_all():
            for session in project.sessions:
                if session.id == session_id:
                    return session
        return None

    def clear_index(self) -> None:
        """Clear all indexed data."""
        import shutil

        if self.index_dir.exists():
            shutil.rmtree(self.index_dir)

        self._indexed_sessions.clear()
        self._vector_store = None
