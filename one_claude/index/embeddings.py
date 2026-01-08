"""Embedding generation for semantic search."""

from pathlib import Path
from typing import Any

import orjson

from one_claude.core.models import MessageType, Session
from one_claude.core.scanner import ClaudeScanner


class EmbeddingGenerator:
    """Generates embeddings using agentd/OpenAI."""

    def __init__(self, model: str = "text-embedding-3-small", cache_dir: Path | None = None):
        self.model = model
        self.cache_dir = cache_dir
        self._client = None
        self._cache: dict[str, list[float]] = {}

        if cache_dir:
            self._load_cache()

    @property
    def available(self) -> bool:
        """Check if embedding generation is available."""
        try:
            from one_claude.llm.client import LLMClient

            client = LLMClient()
            return client.available
        except Exception:
            return False

    def _get_client(self):
        """Get or create LLM client."""
        if self._client is None:
            from one_claude.llm.client import LLMClient

            self._client = LLMClient(embedding_model=self.model)
        return self._client

    def _load_cache(self) -> None:
        """Load embedding cache from disk."""
        if not self.cache_dir:
            return

        cache_file = self.cache_dir / "embeddings_cache.json"
        if cache_file.exists():
            try:
                self._cache = orjson.loads(cache_file.read_bytes())
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        """Save embedding cache to disk."""
        if not self.cache_dir:
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / "embeddings_cache.json"
        cache_file.write_bytes(orjson.dumps(self._cache))

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for text."""
        # Check cache
        cache_key = f"text:{hash(text)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        client = self._get_client()
        embedding = client.embed(text)

        self._cache[cache_key] = embedding
        return embedding

    def embed_session(self, session: Session, scanner: ClaudeScanner) -> list[float]:
        """Generate embedding for entire session."""
        # Check cache
        cache_key = f"session:{session.id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Build session text for embedding
        text = self._build_session_text(session, scanner)

        client = self._get_client()
        embedding = client.embed(text)

        self._cache[cache_key] = embedding
        self._save_cache()
        return embedding

    def _build_session_text(
        self, session: Session, scanner: ClaudeScanner, max_chars: int = 8000
    ) -> str:
        """Build text representation of session for embedding."""
        parts = []

        # Add title
        if session.title:
            parts.append(f"Title: {session.title}")

        # Add project path
        parts.append(f"Project: {session.project_display}")

        # Load and add messages
        tree = scanner.load_session_messages(session)
        messages = tree.get_main_thread()

        total_chars = sum(len(p) for p in parts)

        for msg in messages:
            if msg.type not in (MessageType.USER, MessageType.ASSISTANT):
                continue

            content = msg.text_content[:500]  # Truncate long messages
            if msg.type == MessageType.USER:
                text = f"User: {content}"
            else:
                text = f"Assistant: {content}"
                if msg.tool_uses:
                    tools = ", ".join(t.name for t in msg.tool_uses[:5])
                    text += f" [Tools: {tools}]"

            if total_chars + len(text) > max_chars:
                break

            parts.append(text)
            total_chars += len(text)

        return "\n".join(parts)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        client = self._get_client()
        return client.embed_batch(texts)

    def precompute_session_embeddings(
        self, sessions: list[Session], scanner: ClaudeScanner, progress_callback=None
    ) -> int:
        """Precompute embeddings for multiple sessions."""
        computed = 0

        for i, session in enumerate(sessions):
            cache_key = f"session:{session.id}"
            if cache_key not in self._cache:
                try:
                    self.embed_session(session, scanner)
                    computed += 1
                except Exception:
                    pass

            if progress_callback:
                progress_callback(i + 1, len(sessions), computed)

        self._save_cache()
        return computed
