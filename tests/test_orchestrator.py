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

UNSUPPORTED_TYPE_ASSESSMENT = {
    **CONFIDENT_ASSESSMENT,
    "candidate_visa_types": [
        {"visa_type": "Skilled Worker", "likelihood": 0.9, "reasoning": "Employment sponsorship mentioned."}
    ],
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


def test_high_stakes_flag_escalates_regardless_of_confidence(db):
    orchestrator, _ = _orchestrator([dict(HIGH_STAKES_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-3")

    result = orchestrator.handle_message(db, convo.id, "I was refused a visa once before, want to visit again")

    assert result["escalated"] is True
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.status == ConversationStatus.NEEDS_HUMAN_REVIEW
    assert refreshed.escalations[0].trigger == "advisory_gate_escalation"


def test_unsupported_visa_type_escalates_instead_of_crashing(db):
    orchestrator, _ = _orchestrator([dict(UNSUPPORTED_TYPE_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-4")

    result = orchestrator.handle_message(db, convo.id, "My employer wants to sponsor me to work in the UK")

    assert result["escalated"] is True
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.escalations[0].trigger == "unsupported_visa_type"


def test_escalated_conversation_does_not_resume_autonomous_flow(db):
    orchestrator, _ = _orchestrator([dict(HIGH_STAKES_ASSESSMENT)])
    convo = orchestrator.start_conversation(db, "user-5")
    orchestrator.handle_message(db, convo.id, "I was refused before")

    result = orchestrator.handle_message(db, convo.id, "hello, still there?")

    assert result["status"] == ConversationStatus.NEEDS_HUMAN_REVIEW.value
    assert "caseworker" in result["reply_to_user"].lower()
