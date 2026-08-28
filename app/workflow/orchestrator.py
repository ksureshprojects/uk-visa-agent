"""Top-level per-turn entry point: routes a message to the right phase and
applies the checkpoint gate between them. This is the one place that
"knows" the whole pipeline; everything else (advisory agent, gate, assembly
engine) is independently testable in isolation.
"""

from app.agents.advisory import AdvisoryAgent
from app.agents.models import VisaAssessment
from app.kb.retrieval import KnowledgeStore
from app.llm.base import LLMProvider
from app.storage import repository
from app.storage.models import ConversationStatus, MessageRole

from app.workflow.assembly import AssemblyEngine
from app.workflow.gate import GateDecision, evaluate_checkpoint
from app.workflow.schema import load_schema

# Canonical display names this deployment can label a determination with.
# Every one of these four has a Phase 2 assembly schema (data/schemas/), so a
# determination — forced or confident — always walks the applicant through
# document collection. Any visa type the LLM names that isn't one of these
# four (out of scope for this KB) still gets a complete, KB-grounded verbal
# determination (see _handle_advisory_turn) instead of a schema-driven flow.
_VISA_TYPE_ALIASES = {
    "standard visitor": "Standard Visitor",
    "standard visitor visa": "Standard Visitor",
    "visitor visa": "Standard Visitor",
    "uk standard visitor visa": "Standard Visitor",
    "uk standard visitor": "Standard Visitor",
    "skilled worker": "Skilled Worker",
    "skilled worker visa": "Skilled Worker",
    "uk skilled worker visa": "Skilled Worker",
    "tier 2": "Skilled Worker",
    "tier 2 general": "Skilled Worker",
    "student": "Student",
    "student visa": "Student",
    "uk student visa": "Student",
    "tier 4": "Student",
    "tier 4 general": "Student",
    "family visa": "Family (Partner)",
    "family visa - partner": "Family (Partner)",
    "partner visa": "Family (Partner)",
    "spouse visa": "Family (Partner)",
    "uk family visa": "Family (Partner)",
    "partner/spouse visa": "Family (Partner)",
    "family (partner)": "Family (Partner)",
}

_SCHEMA_SUPPORTED_VISA_TYPES = {"Standard Visitor", "Skilled Worker", "Student", "Family (Partner)"}


def _normalize_visa_type(name: str) -> str:
    return _VISA_TYPE_ALIASES.get(name.strip().lower(), name)


def _best_effort_reply(assessment: VisaAssessment) -> str:
    """Build a reply for GateDecision.FORCE_PASS turns. assessment.reply_to_user
    was generated before the gate decided to force a determination, so it's
    likely still a follow-up question rather than a summary — construct the
    best-effort summary ourselves instead of reusing it verbatim."""
    top = assessment.top_candidate
    parts = [
        f"Based on everything you've told me so far, my best assessment is that this is most likely a "
        f"{top.visa_type} case (about {assessment.confidence:.0%} confidence). {top.reasoning}"
    ]
    if assessment.contradictions:
        parts.append(
            "I noticed what might be conflicting details in what you've told me — "
            + "; ".join(assessment.contradictions)
            + " — let me know if I've misunderstood anything."
        )
    if assessment.missing_info:
        parts.append(
            "A few things are still unconfirmed, which could change this: "
            + "; ".join(assessment.missing_info) + "."
        )
    parts.append("Rather than asking more questions, here's my assessment and what I'd recommend next.")
    return " ".join(parts)


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

        if convo.status in (ConversationStatus.READY_FOR_HUMAN_REVIEW, ConversationStatus.COMPLETED):
            repository.add_message(db, conversation_id, MessageRole.USER, user_text)
            reply = "Your application package is complete and ready for review — let me know if anything changes."
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
            gate_passed=decision in (GateDecision.PASS, GateDecision.FORCE_PASS),
            kb_version=self.kb.version,
        )
        repository.log_audit(
            db, convo.id, "state_transition",
            {
                "gate_decision": decision.value,
                "reason": reason,
                "high_stakes_flags": assessment.high_stakes_flags,
            },
        )

        if decision == GateDecision.CLARIFY:
            repository.increment_clarify_rounds(db, convo.id)
            return {"reply_to_user": assessment.reply_to_user, "status": ConversationStatus.ADVISORY.value}

        # PASS or FORCE_PASS: always deliver a determination, never a human handoff.
        top = assessment.top_candidate
        visa_type = _normalize_visa_type(top.visa_type)
        repository.set_visa_type(db, convo.id, visa_type)

        lead_in = None if decision == GateDecision.PASS else _best_effort_reply(assessment)

        if visa_type in _SCHEMA_SUPPORTED_VISA_TYPES:
            repository.set_status(db, convo.id, ConversationStatus.ASSEMBLY)
            engine = self._assembly_for(visa_type)
            first_prompt = engine.start(db, convo.id)
            reply = first_prompt if lead_in is None else f"{lead_in}\n\n{first_prompt}"
            return {"reply_to_user": reply, "status": ConversationStatus.ASSEMBLY.value, "visa_type": visa_type}

        # No document-assembly schema for this visa type (yet) — the advisory
        # summary itself (system-prompted to be complete: type, reasoning,
        # eligibility, documents, fees, next steps) IS the full service for
        # this category. Conversation stays open for follow-up questions.
        if lead_in is None:
            reply = assessment.reply_to_user
        else:
            reply = lead_in
            repository.add_message(db, convo.id, MessageRole.SYSTEM, reply)
        return {"reply_to_user": reply, "status": ConversationStatus.ADVISORY.value, "visa_type": visa_type}
