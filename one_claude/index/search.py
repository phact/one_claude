"""Search functionality for one_claude."""

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import stringzilla as sz

from one_claude.core.models import Message, MessageTree, MessageType, Project, Session
from one_claude.core.scanner import ClaudeScanner


def _sz_fold(text: str) -> bytes:
    """Case-fold text for case-insensitive matching."""
    return sz.utf8_case_fold(text)


def _sz_find_folded(text_folded: bytes, query_folded: bytes) -> int:
    """Find in already-folded text. Returns -1 if not found."""
    result = sz.find(text_folded, query_folded)
    return -1 if result is None else result


def _sz_count_folded(text_folded: bytes, query_folded: bytes) -> int:
    """Count in already-folded text."""
    return sz.count(text_folded, query_folded)


def _sz_find(text: str, query: str) -> int:
    """Case-insensitive find using stringzilla. Returns -1 if not found."""
    text_folded = _sz_fold(text)
    query_folded = _sz_fold(query)
    return _sz_find_folded(text_folded, query_folded)


@dataclass
class SearchResult:
    """A search result."""

    session: Session
    message: Message | None = None
    score: float = 0.0
    match_type: str = "text"  # "text", "semantic", "combined"
    snippet: str = ""
    matches: list[tuple[int, int]] = field(default_factory=list)  # (start, end) positions


class SearchEngine:
    """Search engine for Claude Code sessions."""

    def __init__(self, scanner: ClaudeScanner, data_dir: Path | None = None):
        self.scanner = scanner
        self.data_dir = data_dir or Path.home() / ".one_claude"
        self._sessions_cache: list[Session] | None = None
        self._last_cache_time: datetime | None = None
        self._embedder = None
        self._vector_store = None
        # Message tree cache for fast search
        self._tree_cache: dict[str, MessageTree] = {}
        self._tree_cache_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_complete = False

    def start_preload(self) -> None:
        """Start background pre-loading of message trees."""
        if self._preload_thread is not None:
            return  # Already running or completed

        self._preload_thread = threading.Thread(target=self._preload_trees, daemon=True)
        self._preload_thread.start()

    def _preload_trees(self) -> None:
        """Pre-load all message trees in background."""
        sessions = self._get_sessions()
        for session in sessions:
            if session.id in self._tree_cache:
                continue
            try:
                tree = self.scanner.load_session_messages(session)
                with self._tree_cache_lock:
                    self._tree_cache[session.id] = tree
            except Exception:
                pass
        self._preload_complete = True

    def _get_tree(self, session: Session) -> MessageTree | None:
        """Get message tree, from cache if available."""
        with self._tree_cache_lock:
            if session.id in self._tree_cache:
                return self._tree_cache[session.id]

        # Not cached, load it
        try:
            tree = self.scanner.load_session_messages(session)
            with self._tree_cache_lock:
                self._tree_cache[session.id] = tree
            return tree
        except Exception:
            return None

    @property
    def preload_progress(self) -> tuple[int, int]:
        """Return (cached_count, total_sessions) for progress tracking."""
        sessions = self._get_sessions()
        with self._tree_cache_lock:
            return len(self._tree_cache), len(sessions)

    def _get_sessions(self, force_refresh: bool = False) -> list[Session]:
        """Get all sessions, with caching."""
        now = datetime.now()
        if (
            force_refresh
            or self._sessions_cache is None
            or self._last_cache_time is None
            or (now - self._last_cache_time).total_seconds() > 60
        ):
            self._sessions_cache = self.scanner.get_sessions_flat()
            self._last_cache_time = now
        return self._sessions_cache

    def search(
        self,
        query: str,
        mode: str = "text",
        project_filter: str | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        """
        Search sessions.

        Args:
            query: Search query
            mode: Search mode - "text", "title", or "content"
            project_filter: Filter by project path (partial match)
            limit: Maximum results to return
        """
        if not query.strip():
            return []

        sessions = self._get_sessions()

        # Apply project filter
        if project_filter:
            sessions = [
                s
                for s in sessions
                if _sz_find(s.project_display, project_filter) >= 0
                or _sz_find(s.project_path, project_filter) >= 0
            ]

        results: list[SearchResult] = []

        if mode == "title":
            results = self._search_titles(query, sessions, limit)
        elif mode == "content":
            results = self._search_content(query, sessions, limit)
        else:  # text - search both
            title_results = self._search_titles(query, sessions, limit)
            content_results = self._search_content(query, sessions, limit)

            # Merge and dedupe by session ID
            seen = set()
            for r in title_results:
                if r.session.id not in seen:
                    r.score *= 1.5  # Boost title matches
                    results.append(r)
                    seen.add(r.session.id)

            for r in content_results:
                if r.session.id not in seen:
                    results.append(r)
                    seen.add(r.session.id)

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _search_titles(
        self, query: str, sessions: list[Session], limit: int
    ) -> list[SearchResult]:
        """Search session titles using stringzilla for speed."""
        results = []
        query_words = query.split()

        for session in sessions:
            title = session.title or ""

            # Calculate match score
            score = 0.0

            # Exact match (case-insensitive)
            start = _sz_find(title, query)
            if start >= 0:
                score = 1.0
                matches = [(start, start + len(query))]
            else:
                # Word match
                matches = []
                for word in query_words:
                    pos = _sz_find(title, word)
                    if pos >= 0:
                        score += 0.3
                        matches.append((pos, pos + len(word)))

            if score > 0:
                results.append(
                    SearchResult(
                        session=session,
                        score=score,
                        match_type="text",
                        snippet=title,
                        matches=matches,
                    )
                )

        return results

    def _search_content(
        self, query: str, sessions: list[Session], limit: int
    ) -> list[SearchResult]:
        """Search session content (messages) using stringzilla for speed."""
        results = []
        query_folded = _sz_fold(query)

        for session in sessions:
            tree = self._get_tree(session)
            if tree is None:
                continue

            messages = tree.all_messages()  # All messages chronologically
            best_match: SearchResult | None = None
            total_matches = 0

            for msg in messages:
                if msg.type not in (MessageType.USER, MessageType.ASSISTANT):
                    continue

                content = msg.text_content
                content_folded = _sz_fold(content)
                idx = _sz_find_folded(content_folded, query_folded)
                if idx >= 0:
                    total_matches += _sz_count_folded(content_folded, query_folded)

                    # Find best snippet
                    start = max(0, idx - 40)
                    end = min(len(content), idx + len(query) + 40)
                    snippet = content[start:end]
                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(content):
                        snippet = snippet + "..."

                    if best_match is None or total_matches > best_match.score:
                        best_match = SearchResult(
                            session=session,
                            message=msg,
                            score=total_matches,
                            match_type="text",
                            snippet=snippet,
                        )

            if best_match:
                results.append(best_match)

        return results

    def search_regex(
        self, pattern: str, sessions: list[Session] | None = None, limit: int = 50
    ) -> list[SearchResult]:
        """Search using regex pattern."""
        if sessions is None:
            sessions = self._get_sessions()

        results = []
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []

        for session in sessions:
            tree = self._get_tree(session)
            if tree is None:
                continue

            messages = tree.all_messages()  # All messages chronologically
            for msg in messages:
                if msg.type not in (MessageType.USER, MessageType.ASSISTANT):
                    continue

                match = regex.search(msg.text_content)
                if match:
                    start = max(0, match.start() - 30)
                    end = min(len(msg.text_content), match.end() + 30)
                    snippet = msg.text_content[start:end]

                    results.append(
                        SearchResult(
                            session=session,
                            message=msg,
                            score=1.0,
                            match_type="regex",
                            snippet=snippet,
                            matches=[(match.start() - start, match.end() - start)],
                        )
                    )
                    break  # One result per session

            if len(results) >= limit:
                break

        return results

    def _get_embedder(self):
        """Get or create embedding generator."""
        if self._embedder is None:
            from one_claude.index.embeddings import EmbeddingGenerator

            cache_dir = self.data_dir / "cache" / "embeddings"
            self._embedder = EmbeddingGenerator(cache_dir=cache_dir)
        return self._embedder

    def _get_vector_store(self):
        """Get or create vector store."""
        if self._vector_store is None:
            from one_claude.index.vector_store import FallbackVectorStore, VectorStore

            store_dir = self.data_dir / "index" / "vectors"
            try:
                self._vector_store = VectorStore(store_dir)
                if not self._vector_store.available:
                    self._vector_store = FallbackVectorStore(store_dir)
            except Exception:
                self._vector_store = FallbackVectorStore(store_dir)
        return self._vector_store

    @property
    def semantic_available(self) -> bool:
        """Check if semantic search is available."""
        try:
            embedder = self._get_embedder()
            return embedder.available
        except Exception:
            return False

    def search_semantic(
        self,
        query: str,
        project_filter: str | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """
        Semantic search using embeddings.

        Requires agentd/openai to be installed and configured.
        """
        embedder = self._get_embedder()
        if not embedder.available:
            return []

        vector_store = self._get_vector_store()

        # Generate query embedding
        try:
            query_embedding = embedder.embed_text(query)
        except Exception:
            return []

        # Search vector store
        matches = vector_store.search(query_embedding, k=limit * 2)

        if not matches:
            return []

        # Build results
        sessions = self._get_sessions()
        session_map = {s.id: s for s in sessions}

        results = []
        for session_id, score in matches:
            session = session_map.get(session_id)
            if not session:
                continue

            # Apply project filter
            if project_filter:
                if (
                    _sz_find(session.project_display, project_filter) < 0
                    and _sz_find(session.project_path, project_filter) < 0
                ):
                    continue

            results.append(
                SearchResult(
                    session=session,
                    score=score,
                    match_type="semantic",
                    snippet=session.title or "",
                )
            )

            if len(results) >= limit:
                break

        return results

    def search_hybrid(
        self,
        query: str,
        project_filter: str | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """
        Hybrid search combining text and semantic.

        Uses Reciprocal Rank Fusion (RRF) to merge results.
        """
        # Get text results
        text_results = self.search(query, mode="text", project_filter=project_filter, limit=limit)

        # Get semantic results if available
        semantic_results = []
        if self.semantic_available:
            semantic_results = self.search_semantic(
                query, project_filter=project_filter, limit=limit
            )

        if not semantic_results:
            return text_results

        # RRF merge
        k = 60  # RRF constant
        rrf_scores: dict[str, float] = {}
        result_map: dict[str, SearchResult] = {}

        for rank, result in enumerate(text_results):
            sid = result.session.id
            rrf_scores[sid] = rrf_scores.get(sid, 0) + 1 / (k + rank)
            if sid not in result_map:
                result_map[sid] = result

        for rank, result in enumerate(semantic_results):
            sid = result.session.id
            rrf_scores[sid] = rrf_scores.get(sid, 0) + 1 / (k + rank)
            if sid not in result_map:
                result_map[sid] = result

        # Sort by RRF score
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        results = []
        for sid in sorted_ids[:limit]:
            result = result_map[sid]
            result.score = rrf_scores[sid]
            result.match_type = "combined"
            results.append(result)

        return results

    def index_session(self, session: Session) -> bool:
        """Index a session for semantic search."""
        embedder = self._get_embedder()
        if not embedder.available:
            return False

        try:
            embedding = embedder.embed_session(session, self.scanner)
            vector_store = self._get_vector_store()
            vector_store.add(session.id, embedding)
            vector_store.save()
            return True
        except Exception:
            return False

    def index_all_sessions(self, progress_callback=None) -> int:
        """Index all sessions for semantic search."""
        embedder = self._get_embedder()
        if not embedder.available:
            return 0

        sessions = self._get_sessions()
        vector_store = self._get_vector_store()
        indexed = 0

        for i, session in enumerate(sessions):
            try:
                embedding = embedder.embed_session(session, self.scanner)
                vector_store.add(session.id, embedding)
                indexed += 1
            except Exception:
                pass

            if progress_callback:
                progress_callback(i + 1, len(sessions), indexed)

        vector_store.save()
        return indexed
