"""The Phase 1 -> Phase 2 checkpoint gate.

Deliberately a pure, deterministic function over the assessment struct, not
another LLM call. This is what makes phase advancement *predictable* and
*testable* rather than a second opinion that could itself be wrong. See
ARCHITECTURE.md section 4.

This gate never hands a case off to a human — there is no caseworker queue
in this deployment, so "the LLM isn't confident enough yet" cannot be a dead
end. It has exactly two ways of moving forward: CLARIFY (ask the user for
more, bounded by MAX_CLARIFY_ROUNDS) or PASS/FORCE_PASS (give the applicant
a determination). FORCE_PASS is what happens once rounds run out without a
confident, cited answer: rather than stalling, the gate commits to the
current top candidate and the orchestrator clearly caveats it as
best-effort.
"""

from enum import Enum

from app.agents.models import VisaAssessment
from app.config import CONFIDENCE_THRESHOLD, MAX_CLARIFY_ROUNDS

AMBIGUITY_MARGIN = 0.15


class GateDecision(str, Enum):
    PASS = "pass"
    FORCE_PASS = "force_pass"
    CLARIFY = "clarify"


def evaluate_checkpoint(
    assessment: VisaAssessment, clarify_rounds_used: int
) -> tuple[GateDecision, str]:
    rounds_exhausted = clarify_rounds_used >= MAX_CLARIFY_ROUNDS
    top = assessment.top_candidate

    if assessment.contradictions:
        detail = ", ".join(assessment.contradictions)
        if rounds_exhausted and top is not None:
            return (
                GateDecision.FORCE_PASS,
                f"Unresolved contradiction(s) after max clarification rounds: {detail} — "
                "proceeding with a caveat rather than stalling.",
            )
        return GateDecision.CLARIFY, f"Unresolved contradiction(s), ask the user to clarify: {detail}"

    if (
        not assessment.ready_for_determination
        or assessment.confidence < CONFIDENCE_THRESHOLD
        or not assessment.citations
    ):
        if rounds_exhausted and top is not None:
            return (
                GateDecision.FORCE_PASS,
                f"Reached max clarification rounds ({MAX_CLARIFY_ROUNDS}) without a fully confident, "
                f"cited determination (confidence={assessment.confidence:.2f}) — giving a best-effort "
                "determination rather than stalling.",
            )
        return GateDecision.CLARIFY, "Insufficient confidence or missing citations; more info needed."

    if top is None:
        # Contradicts its own ready_for_determination=True; treat as not actually ready.
        return GateDecision.CLARIFY, "Model claimed readiness without identifying a candidate visa type."

    close_candidates = [
        c
        for c in assessment.candidate_visa_types
        if c.visa_type != top.visa_type and (top.likelihood - c.likelihood) < AMBIGUITY_MARGIN
    ]
    if close_candidates:
        if rounds_exhausted:
            return (
                GateDecision.FORCE_PASS,
                "Top candidates still too close to confidently distinguish after max clarification "
                "rounds — proceeding with the highest-likelihood candidate rather than stalling.",
            )
        return GateDecision.CLARIFY, "Top candidates too close to confidently distinguish."

    return (
        GateDecision.PASS,
        f"Confident determination: {top.visa_type} (confidence={assessment.confidence:.2f})",
    )
