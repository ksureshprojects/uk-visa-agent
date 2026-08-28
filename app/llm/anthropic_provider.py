from typing import Any

from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_WORKSPACE_ID

from app.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None, workspace_id: str | None = None):
        workspace_id = workspace_id or ANTHROPIC_WORKSPACE_ID
        default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self._client = Anthropic(api_key=api_key or ANTHROPIC_API_KEY, default_headers=default_headers)
        self._model = model or ANTHROPIC_MODEL

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        resp = self._client.messages.create(
            model=self._model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    def structured_complete(
        self,
        system: str,
        messages: list[dict],
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        resp = self._client.messages.create(
            model=self._model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": input_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input
        raise RuntimeError(f"Model did not return expected tool call '{tool_name}'")
