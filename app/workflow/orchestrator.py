"""Top-level per-turn entry point: routes a message to the right phase and
applies the checkpoint gate between them. This is the one place that
"knows" the whole pipeline; everything else (advisory agent, gate, assembly
engine) is independently testable in isolation.
"""

from app.agents.advisory import AdvisoryAgent
from app.kb.retrieval import KnowledgeStore
from app.llm.base import LLMProvider
from app.storage import repository
from app.storage.models import ConversationStatus, MessageRole

from app.workflow.assembly import AssemblyEngine
from app.workflow.gate import GateDecision, evaluate_checkpoint
from app.workflow.schema import load_schema

_KNOWN_VISA_TYPE_ALIASES = {
    "standard visitor",
    "standard visitor visa",
    "visitor visa",
    "uk standard visitor visa",
    "uk standard visitor",
}


def _normalize_visa_type(name: str) -> str:
    if name.strip().lower() in _KNOWN_VISA_TYPE_ALIASES:
        return "Standard Visitor"
    return name


class Orchestrator:
    def __init__(self, llm: LLMProvider, kb: KnowledgeStore):
        self.llm = llm
        self.kb = kb
        self.advisory = AdvisoryAgent(llm, kb)
        self._assembly_engines: dict[str, AssemblyEngine] = {}

    def _assembly_for(self, visa_type: str) -> AssemblyEngine:
        if visa_type not in self._assembly_engines:
            self._assembly_engines[visa_type] = AssemblyEngine(self.llm, load_schema(visa_type))
        return self._assembly_engines[visa_type]

    def start_conversation(self, db, external_user_id: str):
        return repository.create_conversation(db, external_user_id)

    def handle_message(self, db, conversation_id: str, user_text: str) -> dict:
        convo = repository.get_conversation(db, conversation_id)
        if convo is None:
            raise ValueError(f"Unknown conversation: {conversation_id}")

        if convo.status in (ConversationStatus.NEEDS_HUMAN_REVIEW,):
            repository.add_message(db, conversation_id, MessageRole.USER, user_text)
            reply = (
                "Thanks for the message — your case is currently with a caseworker for review. "
                "They'll be in touch; let me know if this is urgent."
            )
            repository.add_message(db, conversation_id, MessageRole.SYSTEM, reply)
            return {"reply_to_user": reply, "status": convo.status.value}

        if convo.status in (ConversationStatus.READY_FOR_HUMAN_REVIEW, ConversationStatus.COMPLETED):
            repository.add_message(db, conversation_id, MessageRole.USER, user_text)
            reply = "Your draft application package is already complete and with a caseworker for review."
            repository.add_message(db, conversation_id, MessageRole.SYSTEM, reply)
            return {"reply_to_user": reply, "status": convo.status.value}

        if convo.status == ConversationStatus.ASSEMBLY:
            engine = self._assembly_for(convo.visa_type)
            result = engine.handle_user_message(db, conversation_id, user_text)
            refreshed = repository.get_conversation(db, conversation_id)
            return {**result, "status": refreshed.status.value}

        return self._handle_advisory_turn(db, convo, user_text)

    def _handle_advisory_turn(self, db, convo, user_text: str) -> dict:
        assessment = self.advisory.handle_user_message(db, convo.id, user_text)
        decision, reason = evaluate_checkpoint(assessment, convo.clarify_rounds_used)

        repository.save_assessment(
            db,
            convo.id,
            candidate_visa_types=[c.model_dump() for c in assessment.candidate_visa_types],
            confidence=assessment.confidence,
            missing_info=assessment.missing_info,
            citations=assessment.citations,
            contradictions=assessment.contradictions,
            gate_passed=decision == GateDecision.PASS,
            kb_version=self.kb.version,
        )
        repository.log_audit(
            db, convo.id, "state_transition", {"gate_decision": decision.value, "reason": reason}
        )

        if decision == GateDecision.CLARIFY:
            repository.increment_clarify_rounds(db, convo.id)
            return {"reply_to_user": assessment.reply_to_user, "status": ConversationStatus.ADVISORY.value}

        if decision == GateDecision.ESCALATE:
            repository.create_escalation(db, convo.id, trigger="advisory_gate_escalation", detail=reason)
            reply = (
                "Thanks for the details. I want to make sure this is handled correctly, so I've passed "
                "your case to a caseworker who will follow up with you directly."
            )
            repository.add_message(db, convo.id, MessageRole.SYSTEM, reply)
            return {
                "reply_to_user": reply,
                "status": ConversationStatus.NEEDS_HUMAN_REVIEW.value,
                "escalated": True,
                "escalation_reason": reason,
            }

        # PASS
        top = assessment.top_candidate
        visa_type = _normalize_visa_type(top.visa_type)

        if visa_type != "Standard Visitor":
            repository.create_escalation(
                db,
                convo.id,
                trigger="unsupported_visa_type",
                detail=f"Determined visa type '{top.visa_type}' has no assembly schema in this demo.",
            )
            reply = (
                f"Based on what you've told me, this looks like a {top.visa_type} case, which needs a "
                "caseworker's direct help today (this demo only automates Standard Visitor Visa "
                "applications). I've flagged your case for a caseworker."
            )
            repository.add_message(db, convo.id, MessageRole.SYSTEM, reply)
            return {
                "reply_to_user": reply,
                "status": ConversationStatus.NEEDS_HUMAN_REVIEW.value,
                "escalated": True,
            }

        repository.set_visa_type(db, convo.id, visa_type)
        repository.set_status(db, convo.id, ConversationStatus.ASSEMBLY)
        engine = self._assembly_for(visa_type)
        first_prompt = engine.start(db, convo.id)
        return {"reply_to_user": first_prompt, "status": ConversationStatus.ASSEMBLY.value, "visa_type": visa_type}
