"""LLM client wrapper using agentd."""

from typing import Any

# Try to import agentd, fall back gracefully
try:
    from agentd import patch_openai_with_mcp
    from openai import OpenAI

    AGENTD_AVAILABLE = True
except ImportError:
    AGENTD_AVAILABLE = False
    OpenAI = None  # type: ignore
    patch_openai_with_mcp = None  # type: ignore


class LLMClient:
    """Wrapper for LLM operations via agentd."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        embedding_model: str = "text-embedding-3-small",
    ):
        self.model = model
        self.embedding_model = embedding_model
        self._client = None

    @property
    def available(self) -> bool:
        """Check if agentd is available."""
        return AGENTD_AVAILABLE

    @property
    def client(self):
        """Get or create the OpenAI client."""
        if not AGENTD_AVAILABLE:
            raise RuntimeError("agentd not installed. Install with: uv pip install agentd openai")

        if self._client is None:
            base_client = OpenAI()
            self._client = patch_openai_with_mcp(base_client)

        return self._client

    def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """Generate a completion."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def embed(self, text: str) -> list[float]:
        """Generate embedding for text."""
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]
