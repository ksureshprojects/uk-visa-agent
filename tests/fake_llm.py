from app.llm.base import LLMProvider


class BatchScriptedLLM(LLMProvider):
    """Returns queued canned {field_name: value} dicts for structured_complete,
    in call order — one dict per Phase 2 batch turn (app/workflow/assembly.py
    calls structured_complete once per handle_user_message, extracting every
    field in that turn's batch together).

    Lets tests drive the Phase 2 state machine deterministically without a
    real model — the point under test is the state machine's control flow
    (batching, conditional requirements, validation, retries, package
    assembly), not extraction quality. Each queued dict only needs to
    contain the fields it means to answer; fields it omits are treated as
    unanswered (None), same as a real model returning null for them.
    """

    def __init__(self, batches: list[dict[str, str | None]]):
        self._batches = list(batches)

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        return ""

    def structured_complete(self, system, messages, tool_name, tool_description, input_schema, max_tokens=1024):
        if not self._batches:
            raise AssertionError("BatchScriptedLLM ran out of queued batches")
        return self._batches.pop(0)


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
