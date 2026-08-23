from app.agents.models import CandidateVisaType, VisaAssessment
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


def test_high_stakes_flag_always_escalates_even_at_high_confidence():
    assessment = CONFIDENT_VISITOR.model_copy(update={"high_stakes_flags": ["prior_refusal"]})
    decision, reason = evaluate_checkpoint(assessment, clarify_rounds_used=0)
    assert decision == GateDecision.ESCALATE
    assert "prior_refusal" in reason


def test_contradiction_escalates():
    assessment = CONFIDENT_VISITOR.model_copy(update={"contradictions": ["dates of travel conflict"]})
    decision, _ = evaluate_checkpoint(assessment, clarify_rounds_used=0)
    assert decision == GateDecision.ESCALATE


def test_low_confidence_asks_to_clarify_before_max_rounds():
    assessment = CONFIDENT_VISITOR.model_copy(update={"confidence": 0.4, "ready_for_determination": False})
    decision, _ = evaluate_checkpoint(assessment, clarify_rounds_used=1)
    assert decision == GateDecision.CLARIFY


def test_low_confidence_escalates_after_max_rounds():
    assessment = CONFIDENT_VISITOR.model_copy(update={"confidence": 0.4, "ready_for_determination": False})
    decision, _ = evaluate_checkpoint(assessment, clarify_rounds_used=3)
    assert decision == GateDecision.ESCALATE


def test_missing_citations_blocks_pass_even_if_confident():
    assessment = CONFIDENT_VISITOR.model_copy(update={"citations": []})
    decision, _ = evaluate_checkpoint(assessment, clarify_rounds_used=1)
    assert decision == GateDecision.CLARIFY


def test_ambiguous_close_candidates_clarify_then_escalate():
    ambiguous = CONFIDENT_VISITOR.model_copy(
        update={
            "candidate_visa_types": [
                CandidateVisaType(visa_type="Standard Visitor", likelihood=0.55, reasoning="x"),
                CandidateVisaType(visa_type="Marriage Visitor", likelihood=0.50, reasoning="y"),
            ]
        }
    )
    decision, _ = evaluate_checkpoint(ambiguous, clarify_rounds_used=1)
    assert decision == GateDecision.CLARIFY

    decision, _ = evaluate_checkpoint(ambiguous, clarify_rounds_used=3)
    assert decision == GateDecision.ESCALATE
