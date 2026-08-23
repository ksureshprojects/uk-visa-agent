import os

from app.llm.base import LLMProvider


def get_llm_provider(name: str | None = None) -> LLMProvider:
    name = name or os.environ.get("LLM_PROVIDER", "anthropic")
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if name == "stub":
        from .stub_provider import StubProvider

        return StubProvider()
    raise ValueError(f"Unknown LLM provider: {name}")
