"""Session summarization using LLM."""

from one_claude.core.models import MessageType, Session
from one_claude.core.scanner import ClaudeScanner
from one_claude.llm.client import LLMClient


class SessionSummarizer:
    """Generates summaries for sessions using LLM."""

    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    def summarize(self, session: Session, scanner: ClaudeScanner) -> str:
        """Generate a summary for a session."""
        if not self.client.available:
            return self._fallback_summary(session, scanner)

        # Load messages
        tree = scanner.load_session_messages(session)
        messages = tree.get_main_thread()

        # Build context from messages
        context = self._build_context(messages)

        # Generate summary
        prompt = f"""Summarize this Claude Code session in 2-3 sentences. Focus on:
- What the user was trying to accomplish
- Key actions taken
- Outcome or current state

Session content:
{context}

Summary:"""

        try:
            return self.client.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
        except Exception as e:
            return f"(Summary unavailable: {e})"

    def generate_title(self, session: Session, scanner: ClaudeScanner) -> str:
        """Generate a short title from the session."""
        if not self.client.available:
            return self._fallback_title(session, scanner)

        # Load first few messages
        tree = scanner.load_session_messages(session)
        messages = tree.get_main_thread()[:5]

        context = self._build_context(messages, max_chars=500)

        prompt = f"""Generate a short title (5-10 words) for this Claude Code session:

{context}

Title:"""

        try:
            title = self.client.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=30,
            )
            return title.strip().strip('"').strip("'")
        except Exception:
            return self._fallback_title(session, scanner)

    def extract_topics(self, session: Session, scanner: ClaudeScanner) -> list[str]:
        """Extract key topics/tags from a session."""
        if not self.client.available:
            return []

        tree = scanner.load_session_messages(session)
        messages = tree.get_main_thread()
        context = self._build_context(messages)

        prompt = f"""Extract 3-5 key topics/tags from this Claude Code session.
Return as comma-separated list.

{context}

Topics:"""

        try:
            result = self.client.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=50,
            )
            return [t.strip() for t in result.split(",")]
        except Exception:
            return []

    def _build_context(self, messages: list, max_chars: int = 2000) -> str:
        """Build context string from messages."""
        parts = []
        total_chars = 0

        for msg in messages:
            if msg.type == MessageType.USER:
                prefix = "User: "
                content = msg.text_content
            elif msg.type == MessageType.ASSISTANT:
                prefix = "Assistant: "
                content = msg.text_content
                if msg.tool_uses:
                    tools = ", ".join(t.name for t in msg.tool_uses)
                    content += f" [Used: {tools}]"
            else:
                continue

            text = f"{prefix}{content[:300]}"
            if total_chars + len(text) > max_chars:
                break

            parts.append(text)
            total_chars += len(text)

        return "\n".join(parts)

    def _fallback_summary(self, session: Session, scanner: ClaudeScanner) -> str:
        """Generate a basic summary without LLM."""
        tree = scanner.load_session_messages(session)
        messages = tree.get_main_thread()

        user_count = sum(1 for m in messages if m.type == MessageType.USER)
        assistant_count = sum(1 for m in messages if m.type == MessageType.ASSISTANT)

        tool_names = set()
        for msg in messages:
            if msg.type == MessageType.ASSISTANT:
                for tool in msg.tool_uses:
                    tool_names.add(tool.name)

        tools_str = ", ".join(sorted(tool_names)[:5]) if tool_names else "none"

        return f"Session with {user_count} user messages and {assistant_count} assistant responses. Tools used: {tools_str}."

    def _fallback_title(self, session: Session, scanner: ClaudeScanner) -> str:
        """Generate title from first message."""
        tree = scanner.load_session_messages(session)
        messages = tree.get_main_thread()

        for msg in messages:
            if msg.type == MessageType.USER and msg.text_content:
                title = msg.text_content[:60]
                if len(msg.text_content) > 60:
                    title += "..."
                return title

        return "Untitled Session"
