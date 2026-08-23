from app.llm.base import LLMProvider


class ScriptedLLM(LLMProvider):
    """Returns queued canned answers for structured_complete, in call order.

    Lets tests drive the Phase 2 state machine deterministically without a
    real model — the point under test is the state machine's control flow
    (validation, retries, escalation, package assembly), not extraction
    quality.
    """

    def __init__(self, answers: list[str | None]):
        self._answers = list(answers)

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        return ""

    def structured_complete(self, system, messages, tool_name, tool_description, input_schema, max_tokens=1024):
        if not self._answers:
            raise AssertionError("ScriptedLLM ran out of queued answers")
        return {"value": self._answers.pop(0)}


class MultiToolScriptedLLM(LLMProvider):
    """Like ScriptedLLM but keyed by tool_name, for tests that drive both the
    advisory agent (submit_visa_assessment) and the assembly engine
    (extract_field_value) through the same orchestrator call."""

    def __init__(self, queues: dict[str, list[dict]]):
        self._queues = {name: list(items) for name, items in queues.items()}

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        return ""

    def structured_complete(self, system, messages, tool_name, tool_description, input_schema, max_tokens=1024):
        queue = self._queues.get(tool_name)
        if not queue:
            raise AssertionError(f"No queued response for tool '{tool_name}'")
        return queue.pop(0)
