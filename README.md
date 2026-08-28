# uk-visa-agent

A portfolio demo of a UK visa case-preparation agent, reachable over
**WhatsApp, email, or a web-chat UI**, spanning four visa categories
(Standard Visitor, Skilled Worker, Student, Family (Partner)). It exists to
demonstrate two things:

1. An agent that behaves like a strong intake consultant over chat —
   verifies who it's talking to, understands the situation, asks the right
   questions, and drives toward a complete, application-ready package.
2. A concrete answer to *"how do you make agentic service delivery
   predictable and production-grade?"* — see **[ARCHITECTURE.md](ARCHITECTURE.md)**
   for the full design (two-phase pipeline, deterministic checkpoint gate,
   identity verification, audit trail, and — in §8 — how the design evolved
   from a single-visa web-chat demo into this). That document is the primary
   deliverable; this README is just how to run the code.

## Setup: environment variables

Copy the variables below into a `.env` file at the repo root (it's
gitignored, and Claude Code itself is configured to never read it — see
`.claude/settings.json`). `app/config.py` loads it automatically on
startup via `python-dotenv`. Everything is optional at the level of "the
server will still start" — each missing group just disables that
capability, as noted below.

```bash
# --- Anthropic (required for any live conversation, web-chat included) ---
ANTHROPIC_API_KEY=sk-ant-...
# Only needed for an identity-linked Console key (one tied to a user, not a
# workspace) — the API rejects those with a 400 unless every request also
# carries the workspace it should act in. Leave unset for a workspace key.
ANTHROPIC_WORKSPACE_ID=

# --- Twilio (required for the WhatsApp channel) ---
# Console: https://console.twilio.com — Account SID + Auth Token are on the
# dashboard; TWILIO_WHATSAPP_FROM is the WhatsApp-enabled sender number
# (the Twilio sandbox number while testing, e.g. "+14155238886").
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=

# --- Gmail (required for the email channel, and for WhatsApp's OTP step —
# verification codes and package summaries always go out over email) ---
# GMAIL_APP_PASSWORD is a Google "app password", NOT your account password:
# 1. Turn on 2-Step Verification: https://myaccount.google.com/security
# 2. Create an app password: https://myaccount.google.com/apppasswords
GMAIL_USER=
GMAIL_APP_PASSWORD=
```

Without `ANTHROPIC_API_KEY`, the server still starts and non-LLM endpoints
work, but sending any message fails against the Anthropic API. Without
`TWILIO_*`, the WhatsApp webhook still accepts requests but sending a reply
fails. Without `GMAIL_USER`/`GMAIL_APP_PASSWORD`, the email poller
background thread doesn't start at all (logged as a warning at startup) —
and since WhatsApp's identity verification step emails a one-time code, a
WhatsApp session can't get past the OTP step either without Gmail
configured.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# set up .env as above, then:
.venv/bin/uvicorn app.api.main:app --reload
```

Open http://localhost:8000 for the web-chat UI — it bypasses identity
verification entirely (it's given a caller-supplied `external_user_id`
directly) so it's the fastest way to exercise the advisory/assembly
pipeline itself. Try starting as a tourist ("I'd like to visit London for
two weeks, tourism, staying in a hotel") to walk through classification
into the deterministic application-assembly flow, or try another category
(e.g. "I have a job offer from a UK company sponsoring a Skilled Worker
visa") — all four visa types now have a full schema and knowledge base.

To exercise WhatsApp/email for real, point Twilio's webhooks
(`/webhooks/twilio/whatsapp`, `/webhooks/twilio/email`) at a publicly
reachable URL for this server — `scripts/start_tunnel.sh` spins up a
Cloudflare quick tunnel for exactly this (see below and ARCHITECTURE.md §8.4).

### Exposing the dev server publicly (Cloudflare quick tunnel)

```bash
ANTHROPIC_API_KEY=sk-ant-... scripts/start_tunnel.sh
```

Starts uvicorn locally and tunnels it through a random,
unguessable `*.trycloudflare.com` URL — no static IP or open firewall port
needed (useful e.g. on a cloud VM). The app has no auth, so that URL is an
open door to anyone who has it; the random hostname is obscurity, not
security — stop the script (Ctrl+C) when you're done testing, and see
ARCHITECTURE.md §8.4 for the full tradeoff.

## Tests

```bash
.venv/bin/pytest tests/ -q
```

All 57 tests run offline against fakes/fixtures — no API key or network
needed. They cover the deterministic checkpoint gate, field validators, the
Phase 2 state machine (including conditional documents and batched
extraction), full orchestrator wiring across both phases, the identity/case
state machine (OTP issuance/expiry/lockout, case selection, per-case
locking), and the messaging adapters (WhatsApp message splitting, email
enquiry filtering, package-summary formatting).

A separate, LLM-backed golden-set eval (requires `ANTHROPIC_API_KEY`)
checks the real model's classification behavior against a handful of
scripted scenarios:

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/eval_classification.py
```

## Key endpoints

| Endpoint | Purpose |
|---|---|
| `POST /conversations` | Start a new web-chat conversation directly (bypasses identity verification) |
| `POST /conversations/{id}/messages` | Send a user message, get the agent's reply + phase status |
| `GET /conversations/{id}` | Full transcript + current status/visa type |
| `POST /webhooks/twilio/whatsapp` | Twilio inbound WhatsApp webhook — routes through identity verification + case selection |
| `POST /webhooks/twilio/email` | Twilio SendGrid Inbound Parse webhook (alternate email path; the email poller below is the one actually running) |
| `GET /admin/escalations` | Legacy human-review queue view — always empty now, since the gate no longer escalates (see ARCHITECTURE.md §8.6) |
| `GET /admin/conversations/{id}/audit` | Full audit trail: every LLM call, retrieval, and state transition |

The email channel is actually driven by a background poller
(`app/messaging/email_poller.py`, started in `app/api/main.py:startup()`
when `GMAIL_USER`/`GMAIL_APP_PASSWORD` are set), not the webhook above —
Gmail has no inbound webhook, so it's polled over IMAP instead.

## Repository layout

See ARCHITECTURE.md §7 for the annotated layout. In short: `app/agents`
(Phase 1), `app/identity` (identity verification + case state machine),
`app/messaging` (WhatsApp/email adapters), `app/workflow` (checkpoint gate +
Phase 2 + orchestrator), `app/kb` (retrieval), `app/llm` (provider
abstraction), `app/storage` (models + audit log), `app/api` (FastAPI + chat
UI + webhooks), `data/kb` (curated knowledge base, 4 visa categories),
`data/schemas` (one requirement schema per visa category).

## Known limitations (deliberate scope, not oversight — see ARCHITECTURE.md §6)

- **Knowledge base caveat**: this environment's network egress blocked
  direct fetches of gov.uk pages during the original KB curation pass, so
  the corpus in `data/kb/` was compiled from cross-checked web-search
  snippets citing official gov.uk URLs, not verified against raw page/rule
  text. Flagged per-file and in `data/kb/INDEX.md`; applies to all four
  visa categories, not just the first one curated. Fine for demonstrating
  the architecture; needs a verification pass with working gov.uk access
  before relying on it for anything beyond that.
- **No human caseworker queue.** The gate used to escalate low-confidence,
  contradictory, or high-stakes cases to a human review queue; since no
  queue is actually staffed in this deployment, it now always resolves to a
  (possibly caveated, best-effort) determination instead of a dead end. See
  ARCHITECTURE.md §8.6 for the reasoning and the trade-off this makes.
- Document checklist items are confirmed by the user in chat (presence/
  description), not OCR-verified — a real deployment would plug document
  processing in at that point in the state machine.
- No auth beyond the identity/case layer's email-OTP verification (see
  "Setup: environment variables" above) — no user accounts, roles, or admin
  authentication on the `/admin/*` endpoints. No deployment config, scaling,
  or fine-tuning either — not the interesting problem for this brief.
