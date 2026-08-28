from app.agents.models import CandidateVisaType, VisaAssessment
from app.config import MAX_CLARIFY_ROUNDS
from app.workflow.gate import GateDecision, evaluate_checkpoint

CONFIDENT_VISITOR = VisaAssessment(
    candidate_visa_types=[CandidateVisaType(visa_type="Standard Visitor", likelihood=0.9, reasoning="x")],
    confidence=0.9,
    citations=["visitor-eligibility-01"],
    ready_for_determination=True,
    reply_to_user="ok",
)


def test_confident_cited_assessment_passes():
    decision, _ = evaluate_checkpoint(CONFIDENT_VISITOR, clarify_rounds_used=1)
    assert decision == GateDecision.PASS


def test_high_stakes_flag_does_not_block_pass_decision():
    # There's no human caseworker to hand high-stakes cases to in this
    # deployment, so the gate itself no longer special-cases them — the
    # flag is still recorded (audit log + advisory system prompt), but it
    # doesn't change PASS/CLARIFY/FORCE_PASS routing.
    assessment = CONFIDENT_VISITOR.model_copy(update={"high_stakes_flags": ["prior_refusal"]})
    decision, reason = evaluate_checkpoint(assessment, clarify_rounds_used=0)
    assert decision == GateDecision.PASS


def test_contradiction_clarifies_before_max_rounds():
    assessment = CONFIDENT_VISITOR.model_copy(update={"contradictions": ["dates of travel conflict"]})
    decision, reason = evaluate_checkpoint(assessment, clarify_rounds_used=0)
    assert decision == GateDecision.CLARIFY
    assert "dates of travel conflict" in reason


def test_contradiction_force_passes_after_max_rounds_instead_of_escalating():
    assessment = CONFIDENT_VISITOR.model_copy(update={"contradictions": ["dates of travel conflict"]})
    decision, _ = evaluate_checkpoint(assessment, clarify_rounds_used=MAX_CLARIFY_ROUNDS)
    assert decision == GateDecision.FORCE_PASS


def test_low_confidence_asks_to_clarify_before_max_rounds():
    assessment = CONFIDENT_VISITOR.model_copy(update={"confidence": 0.4, "ready_for_determination": False})
    decision, _ = evaluate_checkpoint(assessment, clarify_rounds_used=0)
    assert decision == GateDecision.CLARIFY


def test_low_confidence_force_passes_after_max_rounds_instead_of_escalating():
    assessment = CONFIDENT_VISITOR.model_copy(update={"confidence": 0.4, "ready_for_determination": False})
    decision, reason = evaluate_checkpoint(assessment, clarify_rounds_used=MAX_CLARIFY_ROUNDS)
    assert decision == GateDecision.FORCE_PASS
    assert "best-effort" in reason


def test_missing_citations_blocks_pass_even_if_confident():
    assessment = CONFIDENT_VISITOR.model_copy(update={"citations": []})
    decision, _ = evaluate_checkpoint(assessment, clarify_rounds_used=0)
    assert decision == GateDecision.CLARIFY


def test_ambiguous_close_candidates_clarify_then_force_pass():
    ambiguous = CONFIDENT_VISITOR.model_copy(
        update={
            "candidate_visa_types": [
                CandidateVisaType(visa_type="Standard Visitor", likelihood=0.55, reasoning="x"),
                CandidateVisaType(visa_type="Marriage Visitor", likelihood=0.50, reasoning="y"),
            ]
        }
    )
    decision, _ = evaluate_checkpoint(ambiguous, clarify_rounds_used=0)
    assert decision == GateDecision.CLARIFY

    decision, _ = evaluate_checkpoint(ambiguous, clarify_rounds_used=MAX_CLARIFY_ROUNDS)
    assert decision == GateDecision.FORCE_PASS
