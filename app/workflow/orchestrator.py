"""Top-level per-turn entry point: routes a message to the right phase and
applies the checkpoint gate between them. This is the one place that
"knows" the whole pipeline; everything else (advisory agent, gate, assembly
engine) is independently testable in isolation.

Two entry points into this class:
  - `handle_message(db, case_id, conversation_id, text)` — advance one
    already-identified case/thread through the phase pipeline. Unchanged
    in spirit from the pre-multichannel version, just rekeyed onto
    (case_id, conversation_id) instead of a single conversation_id.
  - `route_inbound(db, channel, address, text)` — the channel-agnostic
    front door (MULTICHANNEL.md §5): resolves the raw channel address to
    an Identity, then decides whether this continues an open case,
    completes/starts a cross-channel link (§6), or opens a new case.
    A real WhatsApp/email webhook handler parses its provider's payload
    down to (channel, address, text) and calls this.
"""

import datetime

from app.agents.advisory import AdvisoryAgent
from app.config import MAX_LINK_REQUESTS_PER_CASE_PER_HOUR, MAX_OTP_ATTEMPTS
from app.kb.retrieval import KnowledgeStore
from app.llm.base import LLMProvider
from app.storage import repository
from app.storage.models import CaseStatus, ChannelType, Identity, IdentityRole, MessageRole

from app.workflow.assembly import AssemblyEngine
from app.workflow.gate import GateDecision, evaluate_checkpoint
from app.workflow.linking import extract_case_reference, extract_otp_code, generate_otp_code
from app.workflow.schema import load_schema

_KNOWN_VISA_TYPE_ALIASES = {
    "standard visitor",
    "standard visitor visa",
    "visitor visa",
    "uk standard visitor visa",
    "uk standard visitor",
}

GENERIC_LINK_NOT_FOUND = (
    "I couldn't find an open case with that reference. Double-check it, or just tell me about your "
    "situation and I'll start a new case."
)


def _normalize_visa_type(name: str) -> str:
    if name.strip().lower() in _KNOWN_VISA_TYPE_ALIASES:
        return "Standard Visitor"
    return name


def log_only_otp_sender(identity: Identity, case_reference: str, code: str) -> None:
    """Placeholder delivery for the OTP (MULTICHANNEL.md phase 3 stops
    short of wiring real Twilio send calls). Swap for a transport-backed
    sender once WhatsAppTransport/EmailTransport exist (phase 4-5) —
    everything upstream of this call is channel-agnostic already."""
    print(f"[otp] would send code {code} for case {case_reference} to {identity.channel.value}:{identity.address}")


class Orchestrator:
    def __init__(self, llm: LLMProvider, kb: KnowledgeStore, otp_sender=None):
        self.llm = llm
        self.kb = kb
        self.advisory = AdvisoryAgent(llm, kb)
        self._assembly_engines: dict[str, AssemblyEngine] = {}
        self.otp_sender = otp_sender or log_only_otp_sender

    def _assembly_for(self, visa_type: str) -> AssemblyEngine:
        if visa_type not in self._assembly_engines:
            self._assembly_engines[visa_type] = AssemblyEngine(self.llm, load_schema(visa_type))
        return self._assembly_engines[visa_type]

    # ------------------------------------------------------------------
    # Case lifecycle
    # ------------------------------------------------------------------

    def start_case(self, db, channel: ChannelType, address: str):
        """Explicit "start a case" entry point (used by the web transport,
        which has its own dedicated start-then-message flow rather than a
        single inbound-webhook front door). Returns (case, thread). If the
        identity already has an open case, reuses it instead of forking a
        second one — same v1 rule as route_inbound."""
        identity = repository.find_or_create_identity(db, channel, address)
        open_case = repository.get_open_case_for_identity(db, identity.id)
        if open_case is not None:
            thread = repository.get_or_create_thread(db, open_case.id, identity.id)
            return open_case, thread
        return repository.create_case(db, identity)

    def handle_message(self, db, case_id: str, conversation_id: str, user_text: str) -> dict:
        case = repository.get_case(db, case_id)
        if case is None:
            raise ValueError(f"Unknown case: {case_id}")

        if case.status == CaseStatus.NEEDS_HUMAN_REVIEW:
            repository.add_message(db, conversation_id, MessageRole.USER, user_text)
            reply = (
                "Thanks for the message — your case is currently with a caseworker for review. "
                "They'll be in touch; let me know if this is urgent."
            )
            repository.add_message(db, conversation_id, MessageRole.SYSTEM, reply)
            return {"reply_to_user": reply, "status": case.status.value}

        if case.status in (CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.COMPLETED):
            repository.add_message(db, conversation_id, MessageRole.USER, user_text)
            reply = "Your draft application package is already complete and with a caseworker for review."
            repository.add_message(db, conversation_id, MessageRole.SYSTEM, reply)
            return {"reply_to_user": reply, "status": case.status.value}

        if case.status == CaseStatus.ASSEMBLY:
            engine = self._assembly_for(case.visa_type)
            result = engine.handle_user_message(db, case_id, conversation_id, user_text)
            refreshed = repository.get_case(db, case_id)
            return {**result, "status": refreshed.status.value}

        return self._handle_advisory_turn(db, case, conversation_id, user_text)

    def _handle_advisory_turn(self, db, case, conversation_id: str, user_text: str) -> dict:
        assessment = self.advisory.handle_user_message(db, case.id, conversation_id, user_text)
        decision, reason = evaluate_checkpoint(assessment, case.clarify_rounds_used)

        repository.save_assessment(
            db,
            case.id,
            candidate_visa_types=[c.model_dump() for c in assessment.candidate_visa_types],
            confidence=assessment.confidence,
            missing_info=assessment.missing_info,
            citations=assessment.citations,
            contradictions=assessment.contradictions,
            gate_passed=decision == GateDecision.PASS,
            kb_version=self.kb.version,
        )
        repository.log_audit(
            db, case.id, "state_transition", {"gate_decision": decision.value, "reason": reason}
        )

        if decision == GateDecision.CLARIFY:
            repository.increment_clarify_rounds(db, case.id)
            return {"reply_to_user": assessment.reply_to_user, "status": CaseStatus.ADVISORY.value}

        if decision == GateDecision.ESCALATE:
            repository.create_escalation(db, case.id, trigger="advisory_gate_escalation", detail=reason)
            reply = (
                "Thanks for the details. I want to make sure this is handled correctly, so I've passed "
                "your case to a caseworker who will follow up with you directly."
            )
            repository.add_message(db, conversation_id, MessageRole.SYSTEM, reply)
            return {
                "reply_to_user": reply,
                "status": CaseStatus.NEEDS_HUMAN_REVIEW.value,
                "escalated": True,
                "escalation_reason": reason,
            }

        # PASS
        top = assessment.top_candidate
        visa_type = _normalize_visa_type(top.visa_type)

        if visa_type != "Standard Visitor":
            repository.create_escalation(
                db,
                case.id,
                trigger="unsupported_visa_type",
                detail=f"Determined visa type '{top.visa_type}' has no assembly schema in this demo.",
            )
            reply = (
                f"Based on what you've told me, this looks like a {top.visa_type} case, which needs a "
                "caseworker's direct help today (this demo only automates Standard Visitor Visa "
                "applications). I've flagged your case for a caseworker."
            )
            repository.add_message(db, conversation_id, MessageRole.SYSTEM, reply)
            return {
                "reply_to_user": reply,
                "status": CaseStatus.NEEDS_HUMAN_REVIEW.value,
                "escalated": True,
            }

        repository.set_visa_type(db, case.id, visa_type)
        repository.set_status(db, case.id, CaseStatus.ASSEMBLY)
        engine = self._assembly_for(visa_type)
        first_prompt = engine.start(db, case.id, conversation_id)
        return {"reply_to_user": first_prompt, "status": CaseStatus.ASSEMBLY.value, "visa_type": visa_type}

    # ------------------------------------------------------------------
    # Channel-agnostic inbound router (MULTICHANNEL.md §5-6)
    # ------------------------------------------------------------------

    def route_inbound(self, db, channel: ChannelType, address: str, text: str) -> dict:
        identity = repository.find_or_create_identity(db, channel, address)

        open_case = repository.get_open_case_for_identity(db, identity.id)
        if open_case is not None:
            thread = repository.get_or_create_thread(db, open_case.id, identity.id)
            result = self.handle_message(db, open_case.id, thread.id, text)
            return {**result, "case_reference": open_case.reference}

        pending = repository.get_active_verification_for_identity(db, identity.id)
        otp_code = extract_otp_code(text)
        if pending is not None and otp_code is not None:
            return self._complete_link(db, identity, pending, otp_code)
        if pending is not None:
            return {
                "reply_to_user": (
                    "I'm waiting for the 6-digit verification code I sent — reply with that code, or "
                    "send your case reference again if you'd like a new one."
                ),
                "status": "awaiting_verification",
            }

        reference = extract_case_reference(text)
        if reference is not None:
            return self._start_link(db, identity, reference)

        case, thread = repository.create_case(db, identity)
        result = self.handle_message(db, case.id, thread.id, text)
        return {**result, "case_reference": case.reference}

    def _start_link(self, db, identity: Identity, reference: str) -> dict:
        case = repository.get_case_by_reference(db, reference)
        if case is None or case.status == CaseStatus.COMPLETED:
            return {"reply_to_user": GENERIC_LINK_NOT_FOUND, "status": "unlinked"}

        if repository.is_identity_linked_to_case(db, identity.id, case.id):
            thread = repository.get_or_create_thread(db, case.id, identity.id)
            result = self.handle_message(db, case.id, thread.id, user_text=f"(reconnected via {reference})")
            return {**result, "case_reference": case.reference}

        since = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        if repository.count_recent_link_requests(db, case.id, since) >= MAX_LINK_REQUESTS_PER_CASE_PER_HOUR:
            return {
                "reply_to_user": "Too many verification attempts for this case recently — please try again later.",
                "status": "rate_limited",
            }

        targets = repository.get_linked_identities(db, case.id)
        code = generate_otp_code()
        for target in targets:
            repository.create_link_verification(db, case.id, identity.id, target.id, code)
            self.otp_sender(target, case.reference, code)
        repository.log_audit(
            db,
            case.id,
            "state_transition",
            {"event": "link_otp_sent", "requesting_channel": identity.channel.value},
        )

        channel_desc = (
            targets[0].channel.value if len(targets) == 1 else "channel(s) already linked to this case"
        )
        reply = (
            f"I've sent a 6-digit verification code to the {channel_desc} on file for case {case.reference}. "
            "Reply with that code to continue here."
        )
        return {"reply_to_user": reply, "status": "awaiting_verification"}

    def _complete_link(self, db, identity: Identity, verification, code: str) -> dict:
        ok = repository.verify_link_code(db, verification.id, code)
        if not ok:
            refreshed = repository.get_link_verification(db, verification.id)
            dead = (
                refreshed is None
                or refreshed.consumed_at is not None
                or refreshed.attempt_count >= MAX_OTP_ATTEMPTS
                or datetime.datetime.utcnow() > refreshed.expires_at
            )
            if dead:
                return {
                    "reply_to_user": (
                        "That code didn't work and this verification is no longer valid. Send your case "
                        "reference again for a new code."
                    ),
                    "status": "verification_expired",
                }
            return {"reply_to_user": "That code didn't match — please try again.", "status": "verification_retry"}

        case = repository.get_case(db, verification.case_id)
        repository.link_identity_to_case(db, case.id, identity.id, role=IdentityRole.LINKED)
        thread = repository.get_or_create_thread(db, case.id, identity.id)
        repository.log_audit(
            db, case.id, "state_transition", {"event": "identity_linked", "channel": identity.channel.value}
        )

        reply = self._resume_message(db, case)
        repository.add_message(db, thread.id, MessageRole.SYSTEM, reply)
        return {"reply_to_user": reply, "status": case.status.value, "case_reference": case.reference}

    def _resume_message(self, db, case) -> str:
        if case.status == CaseStatus.ASSEMBLY:
            engine = self._assembly_for(case.visa_type)
            next_req = engine.next_requirement(db, case.id)
            if next_req is not None:
                return f"You're verified for case {case.reference}. Picking up where you left off:\n\n{next_req.prompt}"
            return f"You're verified for case {case.reference}. It's complete and awaiting caseworker review."
        if case.status == CaseStatus.NEEDS_HUMAN_REVIEW:
            return f"You're verified for case {case.reference}. It's currently with a caseworker for review."
        if case.status in (CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.COMPLETED):
            return f"You're verified for case {case.reference}. The application package is complete and with a caseworker."
        return f"You're verified for case {case.reference}. Go ahead and continue telling me about your situation."
