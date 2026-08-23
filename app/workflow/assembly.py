"""Phase 2 — the deterministic application assembly workflow.

This is a finite state machine over the requirement schema, not free LLM
reasoning: the LLM's ONLY role is extracting a single field's value from the
user's latest message (`_extract`). Every extracted value is then run
through a deterministic validator (app/workflow/validators.py); a field is
marked "valid" exclusively by that validator, never by the LLM's own
judgment. Persistent validation failure escalates to a human rather than
looping forever.
"""

from app.config import MAX_VALIDATION_RETRIES
from app.llm.base import LLMProvider
from app.storage import repository
from app.storage.models import ConversationStatus, MessageRole

from app.workflow.schema import Requirement, VisaSchema
from app.workflow.validators import validate

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {
            "type": ["string", "null"],
            "description": "The extracted value for the requested field, normalized per its format (e.g. dates as YYYY-MM-DD). Null if the message doesn't clearly answer this specific question — never guess.",
        }
    },
    "required": ["value"],
}

EXTRACTION_SYSTEM_TEMPLATE = """You are extracting exactly one structured field from a user's WhatsApp message for a UK visa application form.

Field being collected: {name}
Question asked to the user: "{prompt}"

Extract ONLY the value for this field from the user's latest message. If it is a date, normalize it to YYYY-MM-DD. If the message does not clearly answer this specific question, return null — never guess or infer a value that wasn't actually stated."""


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

    def next_requirement(self, db, conversation_id: str) -> Requirement | None:
        context = self._context(db, conversation_id)
        existing = {f.field_name: f for f in repository.get_fields(db, conversation_id)}
        for req in self.schema.requirements:
            if not req.applies(context):
                continue
            existing_field = existing.get(req.name)
            if existing_field is None or existing_field.status != "valid":
                return req
        return None

    def start(self, db, conversation_id: str) -> str:
        """Call once, right after the Phase 1 -> Phase 2 checkpoint gate passes."""
        req = self.next_requirement(db, conversation_id)
        assert req is not None, "requirement schema must have at least one requirement"
        message = (
            "Thanks — I have what I need to start preparing the application package. "
            f"I'll ask a series of specific questions.\n\n{req.prompt}"
        )
        repository.add_message(db, conversation_id, MessageRole.AGENT, message)
        return message

    def _extract(self, req: Requirement, user_text: str) -> str | None:
        system = EXTRACTION_SYSTEM_TEMPLATE.format(name=req.name, prompt=req.prompt)
        raw = self.llm.structured_complete(
            system=system,
            messages=[{"role": "user", "content": user_text}],
            tool_name="extract_field_value",
            tool_description="Extract the requested field's value from the user's message.",
            input_schema=EXTRACTION_SCHEMA,
        )
        return raw.get("value")

    def handle_user_message(self, db, conversation_id: str, user_text: str) -> dict:
        repository.add_message(db, conversation_id, MessageRole.USER, user_text)
        req = self.next_requirement(db, conversation_id)
        if req is None:
            reply = "The application package is already complete and awaiting human review."
            repository.add_message(db, conversation_id, MessageRole.AGENT, reply)
            return {"done": True, "escalated": False, "reply_to_user": reply}

        extracted = self._extract(req, user_text)
        context = self._context(db, conversation_id)
        result = validate(req.validator, extracted, context, req.validator_args)

        repository.log_audit(
            db,
            conversation_id,
            "validation",
            {"field": req.name, "raw_extracted": extracted, "ok": result.ok, "error": result.error},
        )

        if result.ok:
            repository.upsert_field(db, conversation_id, req.name, result.normalized_value, "valid")
            next_req = self.next_requirement(db, conversation_id)
            if next_req is None:
                package = self.build_package(db, conversation_id)
                repository.set_status(db, conversation_id, ConversationStatus.READY_FOR_HUMAN_REVIEW)
                reply = (
                    "All required information and documents are confirmed. I've put together a draft "
                    "application package below — a caseworker will review it before anything is submitted."
                )
                repository.add_message(db, conversation_id, MessageRole.AGENT, reply)
                return {"done": True, "escalated": False, "reply_to_user": reply, "package": package}

            repository.add_message(db, conversation_id, MessageRole.AGENT, next_req.prompt)
            return {"done": False, "escalated": False, "reply_to_user": next_req.prompt}

        field_row = repository.upsert_field(db, conversation_id, req.name, extracted, "invalid", result.error)
        if field_row.retry_count >= MAX_VALIDATION_RETRIES:
            repository.create_escalation(
                db,
                conversation_id,
                trigger="persistent_validation_failure",
                detail=f"Field '{req.name}' failed validation {field_row.retry_count} times: {result.error}",
            )
            reply = (
                "I'm having trouble getting a valid answer for this, so I've flagged your case for a "
                "human caseworker to follow up with you directly."
            )
            repository.add_message(db, conversation_id, MessageRole.SYSTEM, reply)
            return {"done": False, "escalated": True, "reply_to_user": reply}

        reply = f"{result.error} {req.prompt}"
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
