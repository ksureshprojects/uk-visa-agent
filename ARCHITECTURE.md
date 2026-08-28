# Architecture: UK Visa Service Agent

This document is the primary deliverable for the second half of the brief:
*"how do we make agentic service delivery predictable and production-grade?"*
The code in this repo is a thin, working proof of the design described here,
scoped to a single visa category (Standard Visitor Visa) so the pipeline runs
end-to-end rather than being broad and half-built.

## 1. Problem framing

Two very different tasks are hiding inside "help someone get a UK visa":

| | Visa classification | Application assembly |
|---|---|---|
| Nature | Open-ended judgment over an ambiguous situation | Collecting and validating a known, finite set of facts and documents |
| Right tool | An LLM agent, grounded in retrieved rules | A deterministic state machine, with an LLM used only for extraction |
| Failure mode if done wrong | Confidently wrong advice | Silently incomplete/invalid application |
| How we keep it safe | Confidence gating + citations + escalation | Schema validation, never LLM judgment alone |

Treating both as "just ask the agent" is the reliability problem. The design
below keeps them as two phases with an explicit, auditable gate between them.

## 2. Pipeline

```
User message
    │
    ▼
Orchestrator ── logs every turn ──► Conversation Store (SQLite)
    │
    ▼
┌─────────────────────────────┐
│ PHASE 1 — Advisory Agent     │   autonomous agent zone
│ (app/agents/advisory.py)     │
│                               │
│  LLM + RAG retrieval          │──► Knowledge Store (app/kb)
│  over curated, versioned      │    versioned markdown chunks with
│  Immigration Rules extracts   │    citation_id + source_url
│                               │
│  Output: structured           │
│  VisaAssessment{              │
│    candidate_visa_types,      │
│    confidence,                │
│    missing_info,              │
│    citations,                 │
│    contradictions             │
│  }                             │
└──────────────┬────────────────┘
               │
               ▼
      ┌───────────────────┐
      │  CHECKPOINT GATE   │  deterministic, rule-based — never an LLM decision
      └────────┬───────────┘
     pass │              │ fail (low confidence / contradiction /
          │              │ ambiguous type / max clarification rounds hit /
          │              │ high-stakes flag)
          ▼              ▼
┌─────────────────────┐  ┌─────────────────────────┐
│ PHASE 2 — Assembly   │  │ Escalation: human review │
│ (app/workflow)        │  │ queue, conversation       │
│ deterministic zone    │  │ flagged, user told a      │
│                        │  │ caseworker will follow up │
│ Finite state machine   │  └─────────────────────────┘
│ over a JSON schema      │
│ (data/schemas/*.json)   │
│                          │
│ LLM role: extract field  │
│ values from NL text ONLY │
│                          │
│ Validator role: format,  │
│ cross-field, document    │
│ checklist — LLM never    │
│ marks a field "done"     │
└──────────┬───────────────┘
           │ all required fields + docs pass validation
           ▼
┌─────────────────────────┐
│ HUMAN REVIEW GATE         │  always required before "final"
│ produces a submission-    │  (this is a deliberate product
│ ready DRAFT package, never│   boundary — see §5)
│ an auto-submitted one     │
└─────────────────────────┘
```

## 3. Why RAG, not fine-tuning

The Immigration Rules change multiple times a year (Appendix updates, fee
changes, threshold changes). A fine-tuned model bakes facts into weights that
go stale silently and expensively to refresh. RAG over a versioned corpus:

- lets the agent cite the exact rule it relied on (`citation_id` +
  `source_url` + `retrieved_date`) — auditable, not just "the AI said so";
- can be updated by editing/re-embedding markdown files, no retraining;
- makes staleness visible: every stored assessment records which KB version
  it used, so a rule change can be swept for affected past conversations.

Fine-tuning would be the right tool for a *different* problem — matching a
specific consultative tone or question-asking style — not for injecting facts
that must stay current and citeable. Not needed for this demo's scope.

## 4. Reliability mechanisms (the core of part 2 of the brief)

**Checkpoint gate (Phase 1 → Phase 2).** A pure function over the
`VisaAssessment` struct, not a model call: advance only if
`confidence >= THRESHOLD`, zero unresolved contradictions, and every
eligibility claim carries a citation. This is what makes the system
*predictable* rather than merely *plausible* — the gate's logic is testable
and doesn't drift with prompt changes.

**Bounded clarification loop.** Phase 1 may ask at most `MAX_CLARIFY_ROUNDS`
(3) follow-up questions before being forced to either pass the gate or
escalate. Prevents an agent stuck in "just one more question" from stalling
the conversation indefinitely — a concrete, testable reliability property.

**Escalation triggers** (`app/workflow/escalation.py`), all rule-based:
1. Retrieval confidence below threshold / no matching rule found for the
   stated situation.
2. User contradicts a previously stated fact.
3. Visa type still ambiguous between 2+ candidates after max clarification
   rounds.
4. User explicitly asks for a human.
5. High-stakes flags — prior visa refusal, criminal record mention,
   asylum-adjacent language — **always** escalate regardless of confidence,
   because the cost of a wrong autonomous answer is highest exactly here.
6. Deterministic validation failure that persists after N correction
   attempts in Phase 2.

**State snapshotting & recovery.** Every checkpoint (phase transition,
escalation, field update) persists the full structured state — not just chat
history — to the Conversation Store. A human reviewer, or a resumed session,
picks up from that structured state rather than replaying the transcript.
Each write is a single row insert (append-only), so a retried step after a
timeout does not corrupt state.

**Audit trail.** Every LLM call (prompt, retrieved chunks with their
citation ids, raw output), every validator result, and every state
transition is logged to the Conversation Store. This is what makes "service
audits and model improvement" (the second-order goal in the brief) possible
later — you can replay exactly what the agent saw and decided at any point.

**Deterministic-only completion.** Phase 2 fields and documents are marked
complete exclusively by validator functions (regex/format checks, date
logic, cross-field consistency, document-type/recency checks) — never by the
LLM asserting "this looks fine." The LLM's only job in Phase 2 is turning
free text into structured field values — up to 7 fields at once are batched
into a single question, extracted from one reply, and validated
independently — if extraction is ambiguous for a given field, the validator
rejects that field and the agent re-prompts for it specifically.

## 5. Product/compliance boundary

Giving immigration advice in the UK is OISC-regulated (or requires a
qualified solicitor). This system is designed as a **case preparation
assistant**: it never tells a user "you qualify, submit this," and Phase 2's
output is explicitly a *draft package* gated on human review before
anything is filed. This isn't a missing feature — it's a deliberate
boundary reflected in the Human Review Gate in §2, and it's the honest
answer to "how does an autonomous agent stay safe in a regulated domain."

## 6. What's out of scope for this demo (and why that's a scoping decision, not a gap)

- **Real WhatsApp/Telegram integration.** The channel is abstracted behind
  `ChatTransport` (`app/api/transport.py`); only a stub web-chat
  implementation exists today. Channel onboarding (Meta Business API
  approval, message templates, session windows) is a separate, independently
  gated workstream, as noted in the original brief discussion — swapping in
  a real WhatsApp transport doesn't touch the agent/workflow code.
- **Additional visa categories.** The schema-driven design
  (`data/schemas/*.json`) generalizes to Skilled Worker, Student, Family
  visas etc., but only Standard Visitor Visa has a schema and curated KB
  today, to keep the demo a complete, working slice rather than several
  incomplete ones.
- **Auth, deployment, scaling, fine-tuning.** Not the interesting problem
  for this brief; noted so their absence reads as scope control, not
  oversight.
- **KB verification against raw gov.uk text.** This environment's network
  egress blocked direct fetches of gov.uk/legislation.gov.uk pages during
  curation, so `data/kb/` was compiled from cross-checked web-search
  snippets citing official gov.uk URLs rather than the raw page/rule text
  itself. Flagged per-file and in `data/kb/INDEX.md`. Sufficient to
  demonstrate the RAG/citation architecture; a production system would
  re-verify against primary sources before relying on it for real advice.

## 7. Repository layout

```
app/
  agents/       Phase 1 advisory agent, prompt construction, VisaAssessment model
  workflow/      Phase 2 state machine, checkpoint gate, escalation rules
  kb/            KB loading, chunking, embedding, retrieval
  llm/           LLMProvider interface + Anthropic adapter (+ stub second adapter)
  storage/       SQLAlchemy models: conversations, messages, assessments, audit log
  api/           FastAPI app, ChatTransport abstraction, web-chat stub endpoint
data/
  kb/            Curated markdown source chunks (visitor visa rules)
  schemas/       JSON requirement schema(s) per visa type
static/          Minimal single-page chat UI
tests/           Escalation-gate unit tests + classification golden-set eval
```
