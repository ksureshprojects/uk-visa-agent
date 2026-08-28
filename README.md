# UK Visa Agent

A UK visa case-preparation agent reachable over WhatsApp, email, and a
web-chat UI. It supports four visa categories — Standard Visitor, Skilled
Worker, Student, and Family (Partner) — and combines an LLM-driven advisory
phase (classifying the applicant's situation against a curated knowledge
base) with a deterministic application-assembly phase (collecting and
validating the fields and documents a submission requires).

For the system design — the two-phase pipeline, the deterministic
checkpoint gate, the identity/case-management layer, and how the
architecture evolved to its current form — see
**[ARCHITECTURE.md](ARCHITECTURE.md)**. This document covers only what is
needed to run the code: prerequisites, external service setup, and the
workflow a user should expect to experience.

## 1. Prerequisites

- Python 3.10+
- An Anthropic API key (required — the advisory and assembly phases are
  both LLM-backed)
- A Twilio account, if you want to exercise the WhatsApp channel
- A Gmail account with an app password, if you want to exercise the email
  channel, or the OTP step of WhatsApp identity verification (see §3)

The web-chat UI only requires the Anthropic API key.

## 2. External service setup

### 2.1 Anthropic (required)

1. Create an API key in the [Anthropic Console](https://console.anthropic.com).
2. If the key is tied to a specific user rather than a workspace (an
   "identity-linked" Console key), also note the workspace ID — the API
   rejects requests from that kind of key with a 400 unless the workspace
   is supplied explicitly.

### 2.2 Twilio (required only for the WhatsApp channel)

1. Create a Twilio account and open the [Twilio Console](https://console.twilio.com).
2. Note the Account SID and Auth Token from the console dashboard.
3. Enable a WhatsApp-capable sender number. For development, Twilio's
   WhatsApp Sandbox is sufficient (a shared sandbox number, e.g.
   `+14155238886`, that you join by sending a join code from your own
   WhatsApp).
4. Once the server is running and reachable at a public URL (§2.4), set
   that sender's inbound webhook, in the Twilio Console, to:
   `https://<your-public-url>/webhooks/twilio/whatsapp`

### 2.3 Gmail (required for the email channel and for WhatsApp OTP delivery)

The email channel, and the one-time verification code sent during WhatsApp
identity verification (§3), are both sent through a real Gmail mailbox over
SMTP/IMAP — not through a third-party email API.

1. Turn on 2-Step Verification on the Google account you intend to use:
   https://myaccount.google.com/security
2. Create an app password: https://myaccount.google.com/apppasswords. This
   is a Google-generated app password, distinct from the account's login
   password.
3. Use that mailbox's address and app password as `GMAIL_USER` /
   `GMAIL_APP_PASSWORD` (§4). No further setup on Google's side is needed —
   inbound mail is polled over IMAP, not pushed via webhook.

### 2.4 Cloudflare quick tunnel (optional — only needed to receive real webhooks)

Twilio's webhook (§2.2) needs a public URL to reach a server that, in
development, is otherwise only listening on `localhost`. `scripts/start_tunnel.sh`
starts the app and exposes it through a Cloudflare quick tunnel — an
outbound-only connection, so no static IP or open firewall port is
required:

```bash
ANTHROPIC_API_KEY=sk-ant-... scripts/start_tunnel.sh
```

This prints a `https://<random-subdomain>.trycloudflare.com` URL. Use that
URL as the base for the Twilio webhook in §2.2. The application has no
authentication layer of its own, so treat this URL as sensitive for as long
as the tunnel is running, and stop the script (Ctrl+C) once you are done
testing.

## 3. Environment variables

Create a `.env` file at the repository root (already gitignored, and
excluded from Claude Code's file access via `.claude/settings.json`).
`app/config.py` loads it automatically on startup.

```bash
# Anthropic — required
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_WORKSPACE_ID=          # only required for an identity-linked Console key; see §2.1

# Twilio — required only for the WhatsApp channel; see §2.2
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=            # e.g. "+14155238886"

# Gmail — required for the email channel and WhatsApp OTP delivery; see §2.3
GMAIL_USER=
GMAIL_APP_PASSWORD=
```

Each capability degrades independently if its variables are absent:

| Missing | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Server starts; any message that reaches the advisory or assembly LLM fails. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` | WhatsApp webhook still accepts inbound requests, but the server cannot send a reply. |
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` | Email poller background thread does not start (logged as a warning at startup). WhatsApp sessions also cannot complete identity verification, since the OTP code is delivered by email. |

## 4. Installation and running the server

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Set up `.env` as described in §3, then start the server:

```bash
.venv/bin/uvicorn app.api.main:app --reload
```

On startup the server initializes the database, loads and embeds the
knowledge base, and — if Gmail credentials are present — starts the email
poller as a background thread. The web-chat UI is served at
`http://localhost:8000`.

## 5. Expected workflow

The advisory/assembly pipeline is identical across all three channels; what
differs is how a user is identified and how a case is selected before that
pipeline sees a message.

### 5.1 WhatsApp

1. Send an opening message to the configured WhatsApp number.
2. **Identity verification.** The agent asks for an email address, sends a
   one-time verification code to it, and asks for that code in reply.
3. **Case selection.** Once verified, the agent asks whether to start a new
   case or continue an existing one (offering the most recent case tied to
   that email address, or accepting a case reference directly).
4. **Advisory and assembly phases**, as in §5.1, now running against the
   selected case.
5. **Completion.** As in §5.1 — additionally, the completed package is
   emailed as a plain-text summary to the verified email address, since
   that's guaranteed to be reachable regardless of which channel the
   conversation happened over.

A returning user is recognized by their verified email address, not their
phone number, so they can resume a case from a different device or channel
once verified.

### 5.2 Email

1. Send an email to the configured mailbox with "UK" and "visa" both
   present somewhere in the subject or body (a filter against unrelated
   inbox traffic — the mailbox is a real inbox, not a dedicated agent
   address).
2. **Identity is the sender's address** — no OTP step, since sending from
   an address is itself proof of controlling it.
3. **Case selection**, **advisory phase**, **assembly phase**, and
   **completion** proceed as in §5.2, replying by email at each step.

Inbound email is picked up by a background poller (default interval: 15
seconds), not a webhook — Gmail has no inbound webhook mechanism.

## 6. Tests

```bash
.venv/bin/pytest tests/ -q
```

All tests run offline against fakes and fixtures — no API key or network
access required. Coverage includes the checkpoint gate, field validators,
the assembly state machine, full orchestrator wiring, the identity/case
state machine (OTP issuance, expiry, lockout, case selection, per-case
locking), and the messaging adapters (WhatsApp message splitting, email
enquiry filtering, package-summary formatting).

A separate, LLM-backed golden-set evaluation checks the live model's
classification behavior against a set of scripted scenarios (requires
`ANTHROPIC_API_KEY`, makes real API calls):

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/eval_classification.py
```

## 7. API reference

| Endpoint | Purpose |
|---|---|
| `POST /conversations` | Start a web-chat conversation directly (bypasses identity verification). |
| `POST /conversations/{id}/messages` | Send a message on a web-chat conversation; returns the agent's reply and current phase status. |
| `GET /conversations/{id}` | Full transcript, current status, and visa type for a conversation. |
| `POST /webhooks/twilio/whatsapp` | Twilio inbound WhatsApp webhook. |
| `POST /webhooks/twilio/email` | Alternate inbound-email webhook path (SendGrid Inbound Parse format); the email poller (§5.3) is the path actually in use. |
| `GET /admin/conversations/{id}/audit` | Full audit trail for a conversation: every LLM call, retrieval, and state transition. |
| `GET /admin/escalations` | Human-review queue view. Retained for future use; nothing currently routes a case here — see ARCHITECTURE.md §8.6. |

## 8. Repository layout

See ARCHITECTURE.md §7 for the fully annotated layout.

```
app/
  agents/     Advisory-phase agent and prompt construction
  identity/   Identity verification and case-selection state machine
  messaging/  WhatsApp/email send and receive adapters
  workflow/   Checkpoint gate, assembly-phase state machine, orchestrator
  kb/         Knowledge base loading, embedding, and retrieval
  llm/        LLM provider abstraction (Anthropic)
  storage/    Database models and audit log
  api/        FastAPI application, web-chat UI, webhooks
data/
  kb/         Curated knowledge base, by visa category
  schemas/    Requirement schema, one per visa category
static/       Web-chat UI
scripts/      Cloudflare tunnel script, classification eval script
tests/
```

## 9. Known limitations

See ARCHITECTURE.md §6 for the full list and rationale. In brief:

- The knowledge base is compiled directly from live gov.uk pages, but a
  number of specific gaps remain (figures gov.uk doesn't publish as a
  single value, country-specific mechanics, etc.), individually flagged
  per-route in `data/kb/INDEX.md`.
- There is no staffed human-review queue behind `/admin/escalations`; the
  checkpoint gate always resolves to a determination rather than routing to
  one.
- Document checklist items are confirmed by user description, not
  OCR-verified.
- There is no authentication on the `/admin/*` endpoints, and no deployment
  or scaling configuration — out of scope for this project.
