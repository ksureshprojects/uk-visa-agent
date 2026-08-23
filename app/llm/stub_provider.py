from typing import Any

from app.llm.base import LLMProvider


class StubProvider(LLMProvider):
    """Deterministic fake, used by tests/eval to avoid network calls.

    Exists to prove the LLMProvider abstraction is real, not decorative: a
    second real provider (OpenAI, etc.) plugs in the same way, without
    touching agent or workflow code.
    """

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        return "This is a stub response for testing."

    def structured_complete(
        self,
        system: str,
        messages: list[dict],
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "StubProvider does not simulate structured extraction; tests "
            "that need specific structured output should monkeypatch the "
            "agent's provider with a fixture-driven fake instead."
        )
