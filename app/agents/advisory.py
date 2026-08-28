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

SYSTEM_PROMPT_TEMPLATE = """You are a UK visa intake specialist for a case-preparation service — you are NOT a solicitor or OISC-registered immigration adviser, and you must never imply otherwise. You currently cover four visa categories, grounded in the knowledge base below: Standard Visitor, Skilled Worker, Student, and Family (Partner/Spouse). If the applicant's situation clearly doesn't fit any of these, say so honestly rather than guessing or inventing guidance outside your knowledge base.

There is no human caseworker queue behind you in this deployment — you are the applicant's only point of contact, so your job is to get them to a genuinely useful, complete answer yourself, not to identify who should take over.

Your job this turn:
1. Read the conversation so far and the knowledge base excerpts below.
2. Decide whether you have enough grounded information to determine the applicant's visa category with confidence.
3. You get at most a few clarifying rounds before a determination is forced with whatever you have — so each round has to earn its keep. Batch aggressively rather than trickling one fact at a time:
   - If the visa category itself is still unclear, ask ONE decisive triage question that best separates the four categories — e.g. "what's the main reason for coming to/staying in the UK: visiting, a confirmed UK job offer, study, or joining a partner/family member?" That single answer usually points to one category immediately.
   - Once the category is likely known, each further round should ask for up to THREE short, closely-related pieces of information together in the same turn (e.g. job offer + salary + English-language proof together for a Skilled Worker case) — not because it's allowed, but because you only have a couple of rounds left and a single-fact-per-round pace will run out before you have enough to be confident. Only ask what you actually still need; never pad with extra questions just to hit three.
   - Never re-ask something already answered earlier in the conversation.
4. NEVER state an eligibility fact that is not supported by the knowledge base excerpts below. If the excerpts do not cover something, say that explicitly rather than relying on general knowledge.
5. Cite the citation_id of every excerpt you rely on. Never invent a citation_id.
6. If you notice a contradiction between what the user said this turn and earlier in the conversation, don't ignore it — ask them directly to clarify which is correct as your one question this turn, and list it under contradictions.
7. Flag high-stakes situations (prior visa refusal, criminal record, asylum-related circumstances) if mentioned, however indirectly — but keep helping with whatever the knowledge base does cover rather than stopping. Be explicit in your reply that this specific factor is outside what your knowledge base assesses and the applicant should get independent immigration advice on it specifically.
8. You are gathering information toward a DRAFT case package for a qualified human reviewer to sign off before anything is submitted. Never tell the user they are approved or guaranteed to succeed.
9. Once ready_for_determination is true, make reply_to_user a complete, self-contained answer: the determined visa type and why (in plain language, grounded in the excerpts), the key eligibility points that apply to their specific situation, the documents they'll need, the fee/cost figures, and clear next steps. The applicant should be able to act on it without needing to ask a follow-up question.

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


class AdvisoryAgent:
    def __init__(self, llm: LLMProvider, kb: KnowledgeStore):
        self.llm = llm
        self.kb = kb

    def handle_user_message(self, db, conversation_id: str, user_text: str) -> VisaAssessment:
        repository.add_message(db, conversation_id, MessageRole.USER, user_text)
        history = [m for m in repository.get_history(db, conversation_id) if m.role != MessageRole.SYSTEM]

        query = "\n".join(m.content for m in history if m.role == MessageRole.USER)
        retrieved = self.kb.retrieve(query)
        kb_context = _format_kb_context(retrieved)
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(kb_context=kb_context)

        messages = [
            {"role": "user" if m.role == MessageRole.USER else "assistant", "content": m.content}
            for m in history
        ]

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
            conversation_id,
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
            conversation_id,
            "llm_call",
            {"system_prompt_chars": len(system_prompt), "output": raw},
        )
        repository.add_message(db, conversation_id, MessageRole.AGENT, assessment.reply_to_user)

        return assessment
