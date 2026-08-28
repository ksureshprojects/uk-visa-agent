from pathlib import Path

from app.kb.retrieval import KnowledgeStore
from app.storage import repository
from app.storage.models import ConversationStatus
from app.workflow.orchestrator import Orchestrator

from tests.fake_llm import MultiToolScriptedLLM

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"

CONFIDENT_ASSESSMENT = {
    "candidate_visa_types": [
        {"visa_type": "Standard Visitor", "likelihood": 0.9, "reasoning": "Tourism, short stay, funds confirmed."}
    ],
    "confidence": 0.9,
    "missing_info": [],
    "citations": ["fixture-financial-01"],
    "contradictions": [],
    "high_stakes_flags": [],
    "ready_for_determination": True,
    "next_question": None,
    "reply_to_user": "This looks like a Standard Visitor Visa case.",
}

LOW_CONFIDENCE_ASSESSMENT = {
    **CONFIDENT_ASSESSMENT,
    "confidence": 0.3,
    "ready_for_determination": False,
    "citations": [],
    "reply_to_user": "How long do you plan to stay?",
}

HIGH_STAKES_ASSESSMENT = {
    **CONFIDENT_ASSESSMENT,
    "high_stakes_flags": ["prior_refusal"],
    "reply_to_user": "Thanks, let me look into that.",
}

SKILLED_WORKER_ASSESSMENT = {
    **CONFIDENT_ASSESSMENT,
    "candidate_visa_types": [
        {"visa_type": "Skilled Worker", "likelihood": 0.9, "reasoning": "Employment sponsorship mentioned."}
    ],
    "reply_to_user": "This looks like a Skilled Worker case — here's a full summary of what you'll need.",
}

# A visa type genuinely outside this KB's four supported categories (not in
# app.workflow.orchestrator._VISA_TYPE_ALIASES), so it stays unmatched by any
# assembly schema — exercises the fallback path for categories the app
# can't (and shouldn't) automate at all.
NO_SCHEMA_ASSESSMENT = {
    **CONFIDENT_ASSESSMENT,
    "candidate_visa_types": [
        {"visa_type": "Ancestry visa", "likelihood": 0.9, "reasoning": "UK-born grandparent mentioned."}
    ],
    "reply_to_user": "This looks like an Ancestry visa case — here's a full summary of what you'll need.",
}

LOW_CONFIDENCE_SKILLED_WORKER_ASSESSMENT = {
    **SKILLED_WORKER_ASSESSMENT,
    "confidence": 0.4,
    "ready_for_determination": False,
    "citations": [],
    "reply_to_user": "What is your job title and salary?",
}


def _orchestrator(assessment_queue):
    kb = KnowledgeStore(kb_dir=FIXTURE_KB)
    llm = MultiToolScriptedLLM({"submit_visa_assessment": assessment_queue})
    return Orchestrator(llm, kb), kb


def test_confident_assessment_transitions_to_assembly(db):
    orchestrator, _ = _orchestrator([dict(CONFIDENT_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-1")

    result = orchestrator.handle_message(db, convo.id, "I want to visit the UK for two weeks as a tourist")

    assert result["status"] == ConversationStatus.ASSEMBLY.value
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.visa_type == "Standard Visitor"
    assert refreshed.status == ConversationStatus.ASSEMBLY


def test_low_confidence_stays_in_advisory_and_counts_clarify_round(db):
    orchestrator, _ = _orchestrator([dict(LOW_CONFIDENCE_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-2")

    result = orchestrator.handle_message(db, convo.id, "I want to visit the UK")

    assert result["status"] == ConversationStatus.ADVISORY.value
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.clarify_rounds_used == 1


def test_high_stakes_flag_does_not_stop_autonomous_handling(db):
    # No human caseworker queue exists in this deployment — a high-stakes
    # flag is recorded (audit log) but must not block the normal PASS ->
    # Assembly routing or produce any "handed off to a human" reply.
    orchestrator, _ = _orchestrator([dict(HIGH_STAKES_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-3")

    result = orchestrator.handle_message(db, convo.id, "I was refused a visa once before, want to visit again")

    assert result["status"] == ConversationStatus.ASSEMBLY.value
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.status == ConversationStatus.ASSEMBLY
    assert refreshed.escalations == []


def test_visa_type_without_assembly_schema_gets_full_advisory_summary(db):
    # "Ancestry visa" isn't one of the four categories this KB (and its
    # assembly schemas) cover — instead of escalating to a human, the
    # conversation stays in ADVISORY and the LLM's own complete summary
    # (system-prompted to include type, eligibility, documents, fees, next
    # steps) is returned directly.
    orchestrator, _ = _orchestrator([dict(NO_SCHEMA_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-4")

    result = orchestrator.handle_message(db, convo.id, "My grandparent was born in the UK")

    assert result["status"] == ConversationStatus.ADVISORY.value
    assert result["visa_type"] == "Ancestry visa"
    assert result["reply_to_user"] == NO_SCHEMA_ASSESSMENT["reply_to_user"]
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.visa_type == "Ancestry visa"
    assert refreshed.escalations == []


def test_no_schema_visa_type_keeps_conversation_open_for_followups(db):
    orchestrator, _ = _orchestrator([dict(NO_SCHEMA_ASSESSMENT), dict(NO_SCHEMA_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-5")

    orchestrator.handle_message(db, convo.id, "My grandparent was born in the UK")
    second = orchestrator.handle_message(db, convo.id, "What documents do I need?")

    assert second["status"] == ConversationStatus.ADVISORY.value
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.status == ConversationStatus.ADVISORY
    assert refreshed.escalations == []


def test_skilled_worker_confident_assessment_transitions_to_assembly(db):
    # All four supported categories (not just Standard Visitor) now have a
    # Phase 2 assembly schema — a confident Skilled Worker determination
    # should walk straight into document collection too.
    orchestrator, _ = _orchestrator([dict(SKILLED_WORKER_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-6")

    result = orchestrator.handle_message(db, convo.id, "My employer wants to sponsor me to work in the UK")

    assert result["status"] == ConversationStatus.ASSEMBLY.value
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.visa_type == "Skilled Worker"
    assert refreshed.status == ConversationStatus.ASSEMBLY


def test_force_pass_after_max_clarify_rounds_mandatorily_enters_assembly(db):
    # Once MAX_CLARIFY_ROUNDS is exhausted without a confident answer, the
    # gate force-passes rather than looping the same advisory turn forever —
    # and because every supported category now has an assembly schema, that
    # forced pass must land the applicant in ASSEMBLY (not leave them stuck
    # in ADVISORY), with the best-effort caveat prepended to the first
    # assembly prompt.
    from app.config import MAX_CLARIFY_ROUNDS

    queue = [dict(LOW_CONFIDENCE_SKILLED_WORKER_ASSESSMENT) for _ in range(MAX_CLARIFY_ROUNDS + 1)]
    orchestrator, _ = _orchestrator(queue)
    convo = orchestrator.start_conversation(db, "user-7")

    result = None
    for _ in range(MAX_CLARIFY_ROUNDS + 1):
        result = orchestrator.handle_message(db, convo.id, "still figuring out the details")

    assert result["status"] == ConversationStatus.ASSEMBLY.value
    assert "best assessment" in result["reply_to_user"].lower()
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.visa_type == "Skilled Worker"
    assert refreshed.status == ConversationStatus.ASSEMBLY
    assert refreshed.clarify_rounds_used == MAX_CLARIFY_ROUNDS
    assert refreshed.escalations == []
