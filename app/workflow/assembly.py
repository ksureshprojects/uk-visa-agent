"""Phase 2 — the deterministic application assembly workflow.

This is a finite state machine over the requirement schema, not free LLM
reasoning: the LLM's ONLY role is extracting field values from the user's
latest message (`_extract_batch`). Every extracted value is then run
through a deterministic validator (app/workflow/validators.py); a field is
marked "valid" exclusively by that validator, never by the LLM's own
judgment. There's no human to escalate persistent validation failures to,
so a field is simply re-asked — MAX_VALIDATION_RETRIES is still tracked
per field (for the audit trail) but no longer caps how many times we'll try.

Up to MAX_ASSEMBLY_BATCH_SIZE still-applicable requirements are asked in one
message rather than one at a time (see `next_batch`). A requirement whose
Condition depends on a field that isn't answered yet never enters a batch —
Requirement.applies(context) only sees already-*valid* field values, never
an in-flight, unanswered batch — so a question is never presented before the
answer that determines whether it's even relevant is known.
"""

from app.config import MAX_ASSEMBLY_BATCH_SIZE, MAX_VALIDATION_RETRIES
from app.llm.base import LLMProvider
from app.storage import repository
from app.storage.models import ConversationStatus, MessageRole

from app.workflow.schema import Requirement, VisaSchema
from app.workflow.validators import validate


def _extraction_schema(batch: list[Requirement]) -> dict:
    return {
        "type": "object",
        "properties": {
            req.name: {
                "type": ["string", "null"],
                "description": (
                    f'Answer to: "{req.prompt}" Normalize per its expected format (e.g. dates as '
                    "YYYY-MM-DD). Null if the user's message doesn't clearly answer this specific "
                    "question — never guess."
                ),
            }
            for req in batch
        },
        "required": [req.name for req in batch],
    }


def _extraction_system_prompt(batch: list[Requirement]) -> str:
    questions = "\n".join(f'- {req.name}: "{req.prompt}"' for req in batch)
    return f"""You are extracting structured field values from a user's WhatsApp message for a UK visa application form. The user was just asked the following questions together, in one message, and may have answered some, all, or none of them:

{questions}

For each field, extract ONLY the value the user's latest message actually gives for that specific question. If it is a date, normalize it to YYYY-MM-DD. If the message doesn't clearly answer a given question, return null for that field — never guess or infer a value that wasn't actually stated. The user may answer questions in any order, or combine several into one sentence."""


class AssemblyEngine:
    def __init__(self, llm: LLMProvider, schema: VisaSchema):
        self.llm = llm
        self.schema = schema

    def _context(self, db, conversation_id: str) -> dict[str, str]:
        fields = repository.get_fields(db, conversation_id)
        return {f.field_name: f.value for f in fields if f.status == "valid"}

    def _kind_of(self, name: str) -> str:
        for r in self.schema.requirements:
            if r.name == name:
                return r.kind
        return "field"

    def next_batch(self, db, conversation_id: str, limit: int = MAX_ASSEMBLY_BATCH_SIZE) -> list[Requirement]:
        context = self._context(db, conversation_id)
        existing = {f.field_name: f for f in repository.get_fields(db, conversation_id)}
        batch: list[Requirement] = []
        for req in self.schema.requirements:
            if not req.applies(context):
                continue
            existing_field = existing.get(req.name)
            if existing_field is None or existing_field.status != "valid":
                batch.append(req)
                if len(batch) == limit:
                    break
        return batch

    def _format_batch(
        self, intro: str, batch: list[Requirement], errors: dict[str, tuple[str, int]]
    ) -> str:
        lines = []
        for i, req in enumerate(batch, start=1):
            error = errors.get(req.name)
            if error is None:
                lines.append(f"{i}. {req.prompt}")
                continue
            message, retry_count = error
            line = f"{i}. {message} {req.prompt}"
            if retry_count >= MAX_VALIDATION_RETRIES:
                line += " No rush — take your time, and let me know if any part of the question is unclear."
            lines.append(line)
        return f"{intro}\n\n" + "\n".join(lines)

    def start(self, db, conversation_id: str) -> str:
        """Call once, right after the Phase 1 -> Phase 2 checkpoint gate passes."""
        batch = self.next_batch(db, conversation_id)
        assert batch, "requirement schema must have at least one requirement"
        message = self._format_batch(
            "Thanks — I have what I need to start preparing the application package. I'll ask a few "
            "questions at a time — feel free to answer as many as you can in one message.",
            batch,
            errors={},
        )
        repository.add_message(db, conversation_id, MessageRole.AGENT, message)
        return message

    def _extract_batch(self, batch: list[Requirement], user_text: str) -> dict[str, str | None]:
        raw = self.llm.structured_complete(
            system=_extraction_system_prompt(batch),
            messages=[{"role": "user", "content": user_text}],
            tool_name="extract_field_values",
            tool_description="Extract the requested fields' values from the user's message.",
            input_schema=_extraction_schema(batch),
        )
        return {req.name: raw.get(req.name) for req in batch}

    def handle_user_message(self, db, conversation_id: str, user_text: str) -> dict:
        repository.add_message(db, conversation_id, MessageRole.USER, user_text)
        batch = self.next_batch(db, conversation_id)
        if not batch:
            reply = "The application package is already complete and awaiting human review."
            repository.add_message(db, conversation_id, MessageRole.AGENT, reply)
            return {"done": True, "escalated": False, "reply_to_user": reply}

        extracted = self._extract_batch(batch, user_text)
        # Updated in-loop (not just re-read from the DB) so a field validated
        # earlier in this same batch — e.g. intended_arrival_date — is
        # visible to a cross-field validator for a field later in the same
        # batch — e.g. intended_departure_date — without waiting a turn.
        context = self._context(db, conversation_id)
        errors: dict[str, tuple[str, int]] = {}

        for req in batch:
            result = validate(req.validator, extracted.get(req.name), context, req.validator_args)
            repository.log_audit(
                db,
                conversation_id,
                "validation",
                {
                    "field": req.name,
                    "raw_extracted": extracted.get(req.name),
                    "ok": result.ok,
                    "error": result.error,
                },
            )
            if result.ok:
                repository.upsert_field(db, conversation_id, req.name, result.normalized_value, "valid")
                context[req.name] = result.normalized_value
            else:
                field_row = repository.upsert_field(
                    db, conversation_id, req.name, extracted.get(req.name), "invalid", result.error
                )
                errors[req.name] = (result.error, field_row.retry_count)

        next_batch = self.next_batch(db, conversation_id)
        if not next_batch:
            package = self.build_package(db, conversation_id)
            repository.set_status(db, conversation_id, ConversationStatus.READY_FOR_HUMAN_REVIEW)
            reply = (
                "All required information and documents are confirmed. I've put together a draft "
                "application package — a caseworker will review it before anything is submitted."
            )
            repository.add_message(db, conversation_id, MessageRole.AGENT, reply)
            return {"done": True, "escalated": False, "reply_to_user": reply, "package": package}

        intro = (
            "Thanks — a few of those need a second look, here's what's next:"
            if errors
            else "Got it, thanks. Next up:"
        )
        reply = self._format_batch(intro, next_batch, errors)
        repository.add_message(db, conversation_id, MessageRole.AGENT, reply)
        return {"done": False, "escalated": False, "reply_to_user": reply}

    def build_package(self, db, conversation_id: str) -> dict:
        fields = repository.get_fields(db, conversation_id)
        valid = {f.field_name: f.value for f in fields if f.status == "valid"}
        return {
            "visa_type": self.schema.visa_type,
            "fields": {k: v for k, v in valid.items() if self._kind_of(k) == "field"},
            "documents_confirmed": [k for k in valid if self._kind_of(k) == "document"],
            "status": "draft_ready_for_human_review",
        }
