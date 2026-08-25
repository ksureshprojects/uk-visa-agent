"""Phase 1 — the autonomous, RAG-grounded advisory agent.

Its only job is producing a structured, cited VisaAssessment each turn. It
never decides on its own whether to advance to Phase 2 or escalate — that is
the deterministic checkpoint gate's job (app/workflow/gate.py). Keeping
"produce an assessment" and "decide what to do with it" as separate
functions is what lets the gate be unit-tested independent of the LLM.
"""

from app.kb.retrieval import KnowledgeStore, RetrievedChunk
from app.llm.base import LLMProvider
from app.storage import repository
from app.storage.models import MessageRole

from app.agents.models import VISA_ASSESSMENT_SCHEMA, VisaAssessment

SYSTEM_PROMPT_TEMPLATE = """You are a UK visa intake specialist for a case-preparation service — you are NOT a solicitor or OISC-registered immigration adviser, and you must never imply otherwise. You currently only handle the Standard Visitor Visa; if the applicant's situation clearly needs a different visa category, say so and flag it, but do not attempt to advise on categories outside your knowledge base.

Your job this turn:
1. Read the conversation so far and the knowledge base excerpts below.
2. Decide whether you have enough grounded information to determine the applicant's visa category with confidence.
3. If not, ask exactly ONE clear follow-up question — the single most useful thing to learn next.
4. NEVER state an eligibility fact that is not supported by the knowledge base excerpts below. If the excerpts do not cover something, say that explicitly rather than relying on general knowledge.
5. Cite the citation_id of every excerpt you rely on. Never invent a citation_id.
6. Flag high-stakes situations (prior visa refusal, criminal record, asylum-related circumstances) even if only mentioned in passing — these always go to a human caseworker, you must not attempt to resolve them yourself.
7. You are gathering information toward a DRAFT case package for a qualified human reviewer. Never tell the user they are approved, guaranteed to succeed, or that they should submit anything without review.

Knowledge base excerpts (your only permitted source of eligibility facts):
{kb_context}
"""


def _format_kb_context(retrieved: list[RetrievedChunk]) -> str:
    if not retrieved:
        return "(no matching excerpts found for this query)"
    parts = []
    for r in retrieved:
        parts.append(f"[{r.chunk.citation_id}] (source: {r.chunk.source_url})\n{r.chunk.text}")
    return "\n\n".join(parts)


def _format_history_for_llm(history: list) -> list[dict]:
    """Cross-channel context (MULTICHANNEL.md §8): history here spans every
    channel thread of the case, not just one. Tag each turn with its
    originating channel, but only when the case actually has more than one
    thread — no point cluttering the single-channel case that's still the
    overwhelming common one."""
    thread_ids = {m.conversation_id for m in history}
    multi_channel = len(thread_ids) > 1
    formatted = []
    for m in history:
        content = m.content
        if multi_channel:
            content = f"[via {m.conversation.identity.channel.value}] {content}"
        formatted.append({"role": "user" if m.role == MessageRole.USER else "assistant", "content": content})
    return formatted


class AdvisoryAgent:
    def __init__(self, llm: LLMProvider, kb: KnowledgeStore):
        self.llm = llm
        self.kb = kb

    def handle_user_message(self, db, case_id: str, conversation_id: str, user_text: str) -> VisaAssessment:
        repository.add_message(db, conversation_id, MessageRole.USER, user_text)
        history = [m for m in repository.get_case_history(db, case_id) if m.role != MessageRole.SYSTEM]

        query = "\n".join(m.content for m in history if m.role == MessageRole.USER)
        retrieved = self.kb.retrieve(query)
        kb_context = _format_kb_context(retrieved)
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(kb_context=kb_context)

        messages = _format_history_for_llm(history)

        raw = self.llm.structured_complete(
            system=system_prompt,
            messages=messages,
            tool_name="submit_visa_assessment",
            tool_description="Submit your structured assessment of the applicant's situation this turn.",
            input_schema=VISA_ASSESSMENT_SCHEMA,
        )
        assessment = VisaAssessment.model_validate(raw)

        repository.log_audit(
            db,
            case_id,
            "retrieval",
            {
                "query": query,
                "citation_ids": [r.chunk.citation_id for r in retrieved],
                "scores": [r.score for r in retrieved],
                "kb_version": self.kb.version,
            },
        )
        repository.log_audit(
            db,
            case_id,
            "llm_call",
            {"system_prompt_chars": len(system_prompt), "output": raw},
        )
        repository.add_message(db, conversation_id, MessageRole.AGENT, assessment.reply_to_user)

        return assessment
