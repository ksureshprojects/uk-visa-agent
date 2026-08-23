from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Provider-agnostic interface for text generation.

    Every call into an LLM from the rest of the app goes through this
    interface, never a provider SDK directly — that's what lets us swap or
    add providers without touching agent/workflow code.
    """

    @abstractmethod
    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        """Free-form text completion, used for conversational replies."""

    @abstractmethod
    def structured_complete(
        self,
        system: str,
        messages: list[dict],
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Forced structured output conforming to input_schema (JSON schema).

        Used wherever the caller needs a parseable object back (visa
        assessments, extracted field values) rather than prose.
        """
