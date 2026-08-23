"""The Phase 1 -> Phase 2 checkpoint gate.

Deliberately a pure, deterministic function over the assessment struct, not
another LLM call. This is what makes phase advancement *predictable* and
*testable* rather than a second opinion that could itself be wrong. See
ARCHITECTURE.md section 4.
"""

from enum import Enum

from app.agents.models import VisaAssessment
from app.config import CONFIDENCE_THRESHOLD, MAX_CLARIFY_ROUNDS

AMBIGUITY_MARGIN = 0.15


class GateDecision(str, Enum):
    PASS = "pass"
    CLARIFY = "clarify"
    ESCALATE = "escalate"


def evaluate_checkpoint(
    assessment: VisaAssessment, clarify_rounds_used: int
) -> tuple[GateDecision, str]:
    # High-stakes situations always escalate, regardless of confidence: the
    # cost of an autonomous wrong answer is highest exactly here.
    if assessment.high_stakes_flags:
        return (
            GateDecision.ESCALATE,
            f"High-stakes flag(s) detected: {', '.join(assessment.high_stakes_flags)}",
        )

    if assessment.contradictions:
        return (
            GateDecision.ESCALATE,
            f"Unresolved contradiction(s): {', '.join(assessment.contradictions)}",
        )

    rounds_exhausted = clarify_rounds_used >= MAX_CLARIFY_ROUNDS

    if (
        not assessment.ready_for_determination
        or assessment.confidence < CONFIDENCE_THRESHOLD
        or not assessment.citations
    ):
        if rounds_exhausted:
            return (
                GateDecision.ESCALATE,
                f"Reached max clarification rounds ({MAX_CLARIFY_ROUNDS}) without a "
                f"confident, cited determination (confidence={assessment.confidence:.2f}).",
            )
        return GateDecision.CLARIFY, "Insufficient confidence or missing citations; more info needed."

    top = assessment.top_candidate
    if top is None:
        return GateDecision.ESCALATE, "No candidate visa type identified."

    close_candidates = [
        c
        for c in assessment.candidate_visa_types
        if c.visa_type != top.visa_type and (top.likelihood - c.likelihood) < AMBIGUITY_MARGIN
    ]
    if close_candidates:
        if rounds_exhausted:
            return GateDecision.ESCALATE, "Visa type still ambiguous after max clarification rounds."
        return GateDecision.CLARIFY, "Top candidates too close to confidently distinguish."

    return (
        GateDecision.PASS,
        f"Confident determination: {top.visa_type} (confidence={assessment.confidence:.2f})",
    )
