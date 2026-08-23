# uk-visa-agent

A portfolio demo of a WhatsApp-native UK visa case-preparation agent, scoped
to the **Standard Visitor Visa**. It exists to demonstrate two things:

1. An agent that behaves like a strong intake consultant over chat —
   understands the situation, asks the right questions, and drives toward a
   complete, application-ready package.
2. A concrete answer to *"how do you make agentic service delivery
   predictable and production-grade?"* — see **[ARCHITECTURE.md](ARCHITECTURE.md)**
   for the full design (two-phase pipeline, deterministic checkpoint gate,
   escalation rules, audit trail). That document is the primary deliverable;
   this README is just how to run the code.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...   # required for live conversations
.venv/bin/uvicorn app.api.main:app --reload
```

Open http://localhost:8000 for the chat UI. Try starting as a tourist
("I'd like to visit London for two weeks, tourism, staying in a hotel") to
walk through classification into the deterministic application-assembly
flow.

Without `ANTHROPIC_API_KEY` set, the server still starts and all
non-LLM endpoints (`/admin/escalations`, conversation lookup) work, but
sending a chat message will fail against the Anthropic API.

## Tests

```bash
.venv/bin/pytest tests/ -q
```

All 26 tests run offline against fakes/fixtures — no API key or network
needed. They cover the deterministic checkpoint gate, field validators,
the Phase 2 state machine (including conditional documents and the
persistent-failure-escalates path), and full orchestrator wiring across
both phases.

A separate, LLM-backed golden-set eval (requires `ANTHROPIC_API_KEY`)
checks the real model's classification behavior against a handful of
scripted scenarios:

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/eval_classification.py
```

## Key endpoints

| Endpoint | Purpose |
|---|---|
| `POST /conversations` | Start a new conversation |
| `POST /conversations/{id}/messages` | Send a user message, get the agent's reply + phase status |
| `GET /conversations/{id}` | Full transcript + current status/visa type |
| `GET /admin/escalations` | The human review queue — every case flagged for a caseworker |
| `GET /admin/conversations/{id}/audit` | Full audit trail: every LLM call, retrieval, and state transition |

## Repository layout

See ARCHITECTURE.md §7 for the annotated layout. In short: `app/agents`
(Phase 1), `app/workflow` (checkpoint gate + Phase 2 + orchestrator),
`app/kb` (retrieval), `app/llm` (provider abstraction), `app/storage`
(models + audit log), `app/api` (FastAPI + chat UI), `data/kb` (curated
knowledge base), `data/schemas` (requirement schema).

## Known limitations (deliberate scope, not oversight — see ARCHITECTURE.md §6)

- **Knowledge base caveat**: this environment's network egress blocked
  direct fetches of gov.uk pages during KB curation, so the corpus in
  `data/kb/` was compiled from cross-checked web-search snippets citing
  official gov.uk URLs, not verified against raw page/rule text. Flagged
  per-file and in `data/kb/INDEX.md`. Fine for demonstrating the
  architecture; needs a verification pass with working gov.uk access
  before relying on it for anything beyond that.
- WhatsApp/Telegram integration is not implemented — only a web-chat stub
  transport exists, behind the same `ChatTransport` abstraction a real
  channel would use.
- Only the Standard Visitor Visa has a full schema + KB; other visa types
  are correctly detected as out of scope and escalated to a human rather
  than mishandled.
- Document checklist items are confirmed by the user in chat (presence/
  description), not OCR-verified — a real deployment would plug document
  processing in at that point in the state machine.
- No auth, deployment config, or fine-tuning — not the interesting problem
  for this brief.
