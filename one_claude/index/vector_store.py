"""Vector similarity search using usearch or fallback."""

import math
from pathlib import Path

import orjson

# Try to import usearch
try:
    from usearch.index import Index

    USEARCH_AVAILABLE = True
except ImportError:
    USEARCH_AVAILABLE = False
    Index = None  # type: ignore


class VectorStore:
    """Vector similarity search storage."""

    def __init__(self, path: Path, ndim: int = 1536):
        self.path = path
        self.ndim = ndim
        self._index = None
        self._key_to_session: dict[int, str] = {}
        self._session_to_key: dict[str, int] = {}
        self._next_key = 0

        self._load()

    @property
    def available(self) -> bool:
        """Check if usearch is available."""
        return USEARCH_AVAILABLE

    def _load(self) -> None:
        """Load index from disk."""
        self.path.mkdir(parents=True, exist_ok=True)

        mapping_file = self.path / "mappings.json"
        if mapping_file.exists():
            try:
                data = orjson.loads(mapping_file.read_bytes())
                self._key_to_session = {int(k): v for k, v in data.get("key_to_session", {}).items()}
                self._session_to_key = data.get("session_to_key", {})
                self._next_key = data.get("next_key", 0)
            except Exception:
                pass

        if USEARCH_AVAILABLE:
            index_file = self.path / "index.usearch"
            self._index = Index(
                ndim=self.ndim,
                metric="cos",
                dtype="f16",
                connectivity=16,
                expansion_add=128,
                expansion_search=64,
            )
            if index_file.exists():
                try:
                    self._index.load(str(index_file))
                except Exception:
                    pass

    def save(self) -> None:
        """Save index to disk."""
        self.path.mkdir(parents=True, exist_ok=True)

        mapping_file = self.path / "mappings.json"
        data = {
            "key_to_session": self._key_to_session,
            "session_to_key": self._session_to_key,
            "next_key": self._next_key,
        }
        mapping_file.write_bytes(orjson.dumps(data))

        if USEARCH_AVAILABLE and self._index is not None:
            index_file = self.path / "index.usearch"
            self._index.save(str(index_file))

    def add(self, session_id: str, embedding: list[float]) -> None:
        """Add session embedding to index."""
        if session_id in self._session_to_key:
            # Update existing
            key = self._session_to_key[session_id]
        else:
            key = self._next_key
            self._next_key += 1
            self._key_to_session[key] = session_id
            self._session_to_key[session_id] = key

        if USEARCH_AVAILABLE and self._index is not None:
            self._index.add(key, embedding)

    def search(self, query_embedding: list[float], k: int = 10) -> list[tuple[str, float]]:
        """Find k nearest sessions."""
        if not USEARCH_AVAILABLE or self._index is None or len(self._key_to_session) == 0:
            return []

        try:
            matches = self._index.search(query_embedding, k)
            results = []
            for key, distance in zip(matches.keys, matches.distances):
                session_id = self._key_to_session.get(int(key))
                if session_id:
                    # Convert distance to similarity score (1 - distance for cosine)
                    score = 1.0 - float(distance)
                    results.append((session_id, score))
            return results
        except Exception:
            return []

    def remove(self, session_id: str) -> bool:
        """Remove session from index."""
        if session_id not in self._session_to_key:
            return False

        key = self._session_to_key[session_id]
        del self._session_to_key[session_id]
        del self._key_to_session[key]

        # Note: usearch doesn't support removal, would need to rebuild
        return True

    @property
    def size(self) -> int:
        """Number of sessions in index."""
        return len(self._session_to_key)


class FallbackVectorStore:
    """Fallback vector store using brute-force search (no usearch dependency)."""

    def __init__(self, path: Path, ndim: int = 1536):
        self.path = path
        self.ndim = ndim
        self._vectors: dict[str, list[float]] = {}

        self._load()

    @property
    def available(self) -> bool:
        return True

    def _load(self) -> None:
        """Load vectors from disk."""
        self.path.mkdir(parents=True, exist_ok=True)
        vectors_file = self.path / "vectors.json"
        if vectors_file.exists():
            try:
                self._vectors = orjson.loads(vectors_file.read_bytes())
            except Exception:
                pass

    def save(self) -> None:
        """Save vectors to disk."""
        self.path.mkdir(parents=True, exist_ok=True)
        vectors_file = self.path / "vectors.json"
        vectors_file.write_bytes(orjson.dumps(self._vectors))

    def add(self, session_id: str, embedding: list[float]) -> None:
        """Add session embedding."""
        self._vectors[session_id] = embedding

    def search(self, query_embedding: list[float], k: int = 10) -> list[tuple[str, float]]:
        """Find k nearest sessions using cosine similarity."""
        if not self._vectors:
            return []

        scores = []
        for session_id, vector in self._vectors.items():
            score = self._cosine_similarity(query_embedding, vector)
            scores.append((session_id, score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def remove(self, session_id: str) -> bool:
        """Remove session from store."""
        if session_id in self._vectors:
            del self._vectors[session_id]
            return True
        return False

    @property
    def size(self) -> int:
        """Number of sessions in store."""
        return len(self._vectors)
