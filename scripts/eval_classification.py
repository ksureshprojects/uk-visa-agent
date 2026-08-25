#!/usr/bin/env python3
"""Golden-set eval for the Phase 1 advisory agent's checkpoint-gate behavior.

Runs a handful of scripted single-turn scenarios against the REAL LLM
provider and KB (unlike tests/test_gate.py and tests/test_orchestrator.py,
which use fakes to test control flow in isolation). This is what catches
prompt regressions and grounding failures that pure unit tests can't:
"did the real model, against the real corpus, reach the right gate
decision." Intentionally small — a handful of high-signal cases, not
exhaustive coverage, matching the one-day demo scope.

Usage:
    ANTHROPIC_API_KEY=... python scripts/eval_classification.py
"""

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ANTHROPIC_API_KEY
from app.kb.retrieval import KnowledgeStore
from app.storage import repository
from app.storage.db import SessionLocal
from app.storage.models import Base, ChannelType
from app.workflow.gate import GateDecision, evaluate_checkpoint
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@dataclass(frozen=True)
class Scenario:
    name: str
    message: str
    expected_gate: GateDecision


SCENARIOS = [
    Scenario(
        name="clear_tourism_short_stay",
        message=(
            "Hi, I'd like to visit London for two weeks as a tourist in March. "
            "I have £3000 saved, a full-time job I'm returning to, and a hotel booked."
        ),
        expected_gate=GateDecision.PASS,
    ),
    Scenario(
        name="vague_first_message_needs_clarification",
        message="Hi, I want to come to the UK.",
        expected_gate=GateDecision.CLARIFY,
    ),
    Scenario(
        name="prior_refusal_always_escalates",
        message=(
            "I want to visit my sister in Manchester for a week. I should mention I was refused "
            "a UK visa two years ago for a different trip."
        ),
        expected_gate=GateDecision.ESCALATE,
    ),
    Scenario(
        name="work_intent_not_a_visitor_case",
        message="My UK employer wants to sponsor me to relocate and work there full-time.",
        expected_gate=GateDecision.CLARIFY,  # low confidence for Standard Visitor specifically, not a crisp PASS
    ),
]


def _fresh_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def main() -> int:
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set — skipping live eval (this is expected in CI/sandboxed runs).")
        print("Set the key and re-run to exercise the real model against the golden set.")
        return 0

    from app.agents.advisory import AdvisoryAgent
    from app.llm import get_llm_provider

    llm = get_llm_provider("anthropic")
    kb = KnowledgeStore()
    agent = AdvisoryAgent(llm, kb)

    passed = 0
    for scenario in SCENARIOS:
        db = _fresh_session()
        identity = repository.find_or_create_identity(db, ChannelType.WEB, f"eval-{scenario.name}")
        case, thread = repository.create_case(db, identity)
        assessment = agent.handle_user_message(db, case.id, thread.id, scenario.message)
        decision, reason = evaluate_checkpoint(assessment, clarify_rounds_used=0)

        ok = decision == scenario.expected_gate
        passed += ok
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {scenario.name}: expected={scenario.expected_gate.value} got={decision.value} ({reason})")
        if not ok:
            print(f"         assessment={assessment.model_dump()}")

    print(f"\n{passed}/{len(SCENARIOS)} scenarios matched expected gate decision.")
    return 0 if passed == len(SCENARIOS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
