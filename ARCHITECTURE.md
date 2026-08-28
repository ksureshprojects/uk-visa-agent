# Architecture: UK Visa Service Agent

Two-phase pipeline for UK visa case preparation, reachable over WhatsApp,
email, and web-chat, across four visa categories (Standard Visitor, Skilled
Worker, Student, Family (Partner)): an LLM-driven advisory phase
(classification, grounded in RAG) gated into a deterministic
application-assembly phase. §8 records the trade-offs evaluated as the
system grew from a single-category web-chat demo into this.

## 1. Problem framing

| | Visa classification | Application assembly |
|---|---|---|
| Nature | Open-ended judgment over an ambiguous situation | Collecting and validating a known, finite set of facts and documents |
| Right tool | LLM agent, grounded in retrieved rules | Deterministic state machine, LLM used only for extraction |
| Failure mode | Confidently wrong advice | Silently incomplete/invalid application |
| Safety mechanism | Confidence gating + citations | Schema validation, never LLM judgment alone |

Design consequence: two phases, one explicit, auditable gate between them —
not a single agent making every kind of decision.

## 2. Pipeline

A channel/identity layer sits in front of the two-phase pipeline and
resolves *who is messaging and which case they mean* before anything
reaches the advisory agent (§8.5).

```
Inbound WhatsApp / Email / web-chat message
    │
    ▼
┌───────────────────────────────┐
│ IdentitySessionManager          │  channel + identity layer (§8.5)
│ (app/identity/session_manager)  │
│                                  │
│  Email-OTP verification          │──► email delivery: real, via Gmail
│  (WhatsApp only — email sender   │    SMTP (app/messaging/gmail.py)
│  address is proof for the        │
│  email channel itself)           │
│  Case selection (new/existing)   │
│  Per-case lock: WhatsApp + email │
│  on the same case can't race     │
└──────────────┬───────────────────┘
               │ case resolved, message handed to its Conversation
               ▼
Orchestrator ── logs every turn ──► Conversation Store (SQLite)
    │
    ▼
┌─────────────────────────────┐
│ PHASE 1 — Advisory Agent     │   autonomous agent zone
│ (app/agents/advisory.py)     │
│                               │
│  LLM + RAG retrieval          │──► Knowledge Store (app/kb)
│  over curated, versioned      │    versioned markdown chunks,
│  Immigration Rules extracts,  │    citation_id + source_url,
│  4 visa categories             │    semantic embedding retrieval (§8.1)
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
     confident/cited │        │ not yet confident (low confidence /
                     │        │ contradiction / ambiguous type),
                     │        │ rounds remaining
                     │        ▼
                     │  ┌───────────────────────┐
                     │  │ CLARIFY: batched        │──┐ loops back to
                     │  │ follow-up question,     │  │ Advisory Agent
                     │  │ bounded by               │  │ for the next
                     │  │ MAX_CLARIFY_ROUNDS (3)   │  │ turn
                     │  └───────────────────────┘◄─┘
                     │        │ rounds exhausted, still not confident
                     │        ▼
                     │  FORCE_PASS: commit to top candidate,
                     │  reply caveats it as best-effort (§8.6)
                     ▼        │
              PASS ◄──────────┘
               │
               ▼
┌──────────────────────────┐
│ Visa type has a Phase 2   │──no──► Advisory summary IS the service for
│ assembly schema? (all 4   │        this category — conversation stays
│ supported categories do)  │        open for follow-up (§8.6)
└──────────────┬────────────┘
               │ yes
               ▼
┌───────────────────────────┐
│ PHASE 2 — Assembly          │
│ (app/workflow)               │
│ deterministic zone            │
│ Finite state machine over a    │
│ JSON schema (data/schemas/,     │
│ one per visa type)               │
│                                    │
│ LLM: extracts ≤7 field values      │
│ per message from NL text ONLY       │
│ (batched)                            │
│                                        │
│ Validator: format, cross-field,        │
│ document checklist — never the LLM.     │
│ Failing field is re-asked (§8.6)         │
└──────────────┬─────────────────────────────┘
               │ all required fields + docs pass validation
               ▼
┌─────────────────────────┐
│ HUMAN REVIEW GATE         │  always required before "final" — the one
│ produces a submission-    │  human checkpoint in this system (§5). Also
│ ready DRAFT package, never│  emailed to the user directly
│ an auto-submitted one.    │  (app/messaging/package_summary.py), even on
└─────────────────────────┘  a WhatsApp case.
```

## 3. Why RAG, not fine-tuning

- The Immigration Rules change multiple times a year (appendix, fee,
  threshold updates); a fine-tuned model bakes facts into weights that go
  stale silently.
- RAG over a versioned corpus: citable (`citation_id` + `source_url` +
  `retrieved_date`), updatable by editing/re-embedding markdown with no
  retraining, and staleness-visible — every stored assessment records the
  `kb_version` it used.
- Fine-tuning is the right tool for a different problem (consultative tone,
  question style), not for facts that must stay current and citeable — not
  needed at this scope.

## 4. Reliability mechanisms

- **Checkpoint gate** (`app/workflow/gate.py`) — pure function over
  `VisaAssessment`, not a model call. Requires confidence ≥ threshold, zero
  unresolved contradictions, and a citation on every eligibility claim.
- **Bounded clarification** — ≤ `MAX_CLARIFY_ROUNDS` (3), each batching up
  to 3 related questions.
- **Gate outcomes**, all rule-based, no dead end (no human queue exists —
  §8.6):
  - `CLARIFY` — insufficient confidence/citations, unresolved contradiction,
    or ambiguous top candidates, with rounds remaining.
  - `FORCE_PASS` — same triggers, rounds exhausted: commits to the top
    candidate, reply explicitly caveated as best-effort
    (`_best_effort_reply`).
  - `PASS` — confidence at/above threshold, cited, uncontradicted, clear
    top candidate.
- **`high_stakes_flags`** (prior refusal, criminal record, asylum-adjacent
  language) — extracted and audit-logged on every turn, no longer a
  distinct gate path (§8.6).
- **Phase 2 validation** — a field that fails validation is re-asked
  (gentler prompt after `MAX_VALIDATION_RETRIES`), never escalated; retry
  count is tracked for audit only.
- **State snapshotting** — every checkpoint (phase transition, field
  update) persists full structured state as an append-only row, not just
  chat history. The identity/case layer mirrors this with its own
  append-only `ChannelMessage` log (§8.5).
- **Audit trail** — every LLM call (prompt, retrieved chunks + citation
  ids, output), validator result, and state transition is logged to the
  Conversation Store.
- **Deterministic-only completion** — Phase 2 fields/documents are marked
  complete exclusively by validator functions; the LLM's only role is
  extracting field values, never judging completeness.

## 5. Product/compliance boundary

Giving immigration advice in the UK is OISC-regulated (or requires a
qualified solicitor). This system is a **case preparation assistant**: it
never tells a user "you qualify, submit this," and Phase 2 output is
explicitly a *draft package* gated on human review before anything is
filed.

The scope of that boundary narrowed as the gate evolved (§8.6): the
original design also routed low-confidence/high-stakes *advisory* turns to
a human caseworker before any determination was given. That path no longer
exists — Phase 1 always resolves to a (possibly best-effort, caveated)
determination. The Human Review Gate on the Phase 2 output — nothing is
ever auto-submitted — is unchanged and is the system's compliance backstop.

## 6. Out of scope

- **Auth, deployment, scaling, fine-tuning** — scope control, not
  oversight.
- **A staffed human caseworker queue.** `EscalationRecord`
  (`app/storage/models.py`) and `repository.create_escalation` exist in the
  schema; nothing calls the latter (§8.6).
- **Document OCR/verification.** Checklist items are confirmed by user
  description in chat, not OCR-verified.
- **Channels beyond WhatsApp/email/web-chat.** A further channel needs its
  own webhook handler and a `ChannelType` entry; reuses the identity/case/
  advisory/assembly stack unchanged.
- **Remaining KB gaps.** All four categories are compiled directly from
  live gov.uk pages (`data/kb/INDEX.md`'s "Recompile note"), not
  search-result snippets. What remains is a set of specific, per-route
  flagged gaps (e.g. no single published Skilled Worker salary figure,
  some country-specific mechanics not researched) rather than a blanket
  verification caveat — see `data/kb/INDEX.md` and individual chunk files.
- **SendGrid-based email sending.** `app/messaging/twilio_client.send_email`
  targets SendGrid's Mail Send API but is a stub pending credentials. All
  real email today (OTP codes, replies, package summaries) goes through
  `app/messaging/gmail.py`'s SMTP client (§8.3, §8.5).

## 7. Repository layout

```
app/
  agents/       Phase 1 advisory agent, prompt construction, VisaAssessment model
  identity/     Identity/case state machine (session_manager.py) + per-case
                locking (case_locks.py) sitting in front of the pipeline — §8.5
  messaging/    Channel adapters: gmail.py (real SMTP/IMAP), twilio_client.py
                (real WhatsApp, stub SendGrid email, §8.3), email_poller.py
                (IMAP polling + enquiry filtering, §8.2), package_summary.py
  workflow/     Phase 2 state machine, checkpoint gate (no escalation, §8.6)
  kb/           KB loading, chunking, embedding, retrieval — semantic
                embeddings via fastembed, not TF-IDF (§8.1)
  llm/          LLMProvider interface + Anthropic adapter (+ stub second adapter)
  storage/      SQLAlchemy models: conversations/messages/assessments/audit
                log (advisory pipeline) + users/cases/sessions/channel
                messages (identity layer, §8.5)
  api/          FastAPI app, ChatTransport abstraction + web-chat stub
                endpoint, webhooks.py (Twilio inbound WhatsApp/email)
data/
  kb/           Curated markdown source chunks, 4 visa categories
  schemas/      JSON requirement schema per visa type (4 today)
static/         Minimal single-page chat UI
scripts/        start_tunnel.sh — Cloudflare quick tunnel (§8.4)
tests/          Checkpoint-gate, validator, state-machine, identity-session,
                messaging-adapter unit tests + classification golden-set eval
```

## 8. Design trade-offs since the initial version

The initial commit shipped one visa category, one channel (web-chat stub),
no identity layer, and a gate that escalated to a human for anything it
wasn't confident about. Each item below states the constraint, the
decision, and the trade-off accepted.

### 8.1 Retrieval matching

- **Constraint.** The original `Vectorizer` (`app/kb/embeddings.py`) was
  hand-rolled TF-IDF cosine similarity — no embedding API dependency, fine
  for one visa type's hand-matched vocabulary. It ranks on literal word
  overlap, so a query like "my girlfriend" scores low against a KB chunk
  written as "partner" even when they mean the same eligibility rule — a
  real problem once the KB covers four categories.
- **Decision.** Swap to `fastembed` running a small quantized transformer
  (`BAAI/bge-small-en-v1.5`) on-device via `onnxruntime` — zero network
  calls at inference, one-off model download cached to
  `EMBEDDING_MODEL_CACHE_DIR` (`.model_cache/`, gitignored). Ranks by
  semantic similarity, so synonymous/colloquial phrasing still retrieves
  the right chunk. `RETRIEVAL_TOP_K` raised 4 → 6 (`app/config.py`) to give
  a query enough headroom across four categories' worth of chunks.
- **Trade-off accepted.** A larger dependency (`onnxruntime` + a cached
  model file) and a one-time download at first run, in exchange for
  retrieval quality that holds up across a multi-category KB.

### 8.2 Email channel: inbound filtering

- **Constraint.** The email channel polls a real mailbox
  (`app/messaging/email_poller.py`), not a dedicated agent-only address —
  most unread mail (newsletters, notifications, unrelated correspondence)
  is not a case enquiry, and running it through identity verification and
  the advisory agent would be wrong behavior and a wasted LLM call.
- **Decision.** Two filter layers: `gmail.fetch_unread(text_filter_terms=...)`
  as a server-side IMAP pre-filter (avoids downloading full bodies for a
  large `UNSEEN` backlog just to discard them), then
  `_looks_like_visa_enquiry` requiring both "uk" and "visa" as whole words
  in subject or body as the actual gate. Non-matching messages are logged
  and left unread, not consumed.
- **Trade-off accepted.** A real enquiry that doesn't use either word is
  missed rather than mishandled — considered the safer failure mode for an
  unauthenticated inbox.

### 8.3 Twilio integration: real WhatsApp sending

- **Constraint.** `send_whatsapp` calls Twilio's Messaging API for real
  (`TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM`).
  Twilio's WhatsApp API rejects an over-limit body outright (error 21617)
  rather than auto-segmenting like plain SMS — a constraint the original
  web-chat-only design didn't have.
- **Decision.** `_split_message` breaks long replies (a batched Phase 2
  question, a full advisory determination) into multiple sends, preferring
  paragraph then word boundaries so no chunk cuts mid-word.
  `send_email` on the same client targets Twilio's SendGrid Mail Send API
  but is left a stub; real email sends go through `app/messaging/gmail.py`
  instead (§8.5) — simpler to stand up than a verified SendGrid sender
  identity for this scope.
- **Trade-off accepted.** Two different email-sending code paths exist
  (SendGrid stub, Gmail SMTP live) rather than one unified path, in
  exchange for not blocking WhatsApp delivery on SendGrid onboarding.

### 8.4 Cloudflare quick tunnel

- **Constraint.** Testing WhatsApp/email webhooks needs a public URL for
  Twilio/SendGrid to POST to; this deployment runs without a static IP or
  an open firewall port (e.g. a GCE VM).
- **Decision.** `scripts/start_tunnel.sh` runs the app on `127.0.0.1` and
  pipes it through `cloudflared tunnel --url` — an outbound-only
  connection to Cloudflare's edge, no inbound firewall rule needed.
- **Trade-off accepted.** The app has no auth, so the resulting
  `*.trycloudflare.com` URL is an open door to anyone who has it. The only
  mitigation is that a quick-tunnel hostname is randomly generated and
  unpublished — obscurity, not security, sufficient only for throwaway
  manual testing (documented in the script itself).

### 8.5 Identity verification: email-OTP workflow

- **Constraint.** The original design had no notion of "who is this" — the
  only channel was a stub taking a caller-supplied `external_user_id`
  directly. A WhatsApp phone number or inbound email address is an
  identifier, not proof of identity; a case-preparation assistant handling
  immigration details can't let a phone number alone pull up someone else's
  case.
- **Decision.** `IdentitySessionManager` (`app/identity/session_manager.py`)
  adds a state machine on `UserSession.state` in front of the advisory
  pipeline:
  - WhatsApp: `AWAITING_EMAIL` → (6-digit code emailed via
    `gmail.send_email`; `OTP_LENGTH` / `OTP_EXPIRY_MINUTES` /
    `OTP_MAX_ATTEMPTS` in `app/config.py`) → `AWAITING_OTP` → (correct code)
    `AWAITING_CASE_CHOICE`. Expiry or too many attempts loops back to
    `AWAITING_EMAIL`, not a permanent lockout.
  - Email: no OTP — the sender's `From` address is already proof of control
    of that inbox; goes straight to case selection scoped to that sender.
  - Both converge on one case-selection sub-machine (new / resume most
    recent / reference an existing id / pick from a numbered list). Once a
    case is `ACTIVE`, the message that started the session is handed
    straight to the advisory pipeline rather than discarded.
  - `User`/`Case`/`UserSession`/`ChannelMessage` (`app/storage/models.py`)
    are a separate model group from `Conversation`/`Message`: one answers
    *who is messaging and which case*, the other *what the advisory
    pipeline decided for that case's conversation*. A `Case` lazily gets a
    `Conversation` only once `ACTIVE`.
  - `app/identity/case_locks.py` adds a per-case lock, since the email
    poller (background thread) and a WhatsApp webhook (request-pool
    thread) can be mid-processing at the same instant — prevents duplicate
    answers or double-incremented clarify rounds on the same case.
    Different cases still process fully in parallel.
- **Trade-off accepted.** Every WhatsApp session now costs one extra
  round-trip (email + OTP) before reaching the advisory agent, in exchange
  for not letting a bare phone number impersonate an existing case owner.

### 8.6 Gate outcomes: escalation → always-a-determination

- **Constraint.** The original gate (`GateDecision.ESCALATE`) routed
  low-confidence determinations, unresolved contradictions, ambiguous visa
  types, and any high-stakes flag — unconditionally — to
  `ConversationStatus.NEEDS_HUMAN_REVIEW`. That model assumes a staffed
  caseworker queue. This deployment has none: no caseworker UI, no
  notification, nothing consuming `EscalationRecord` rows — so an escalated
  conversation was a dead end from the user's side, told "a caseworker will
  follow up" with no caseworker who ever would.
- **Decision.** `GateDecision` is now only `CLARIFY` or `PASS`/`FORCE_PASS`
  (`app/workflow/gate.py`). `CLARIFY` is unchanged, bounded by
  `MAX_CLARIFY_ROUNDS`. Once rounds are exhausted without a confident,
  cited, uncontradicted, unambiguous result, the gate **force-passes**:
  commits to the top candidate, and the reply
  (`_best_effort_reply`, `app/workflow/orchestrator.py`) explicitly
  caveats it as best-effort and surfaces known contradictions/missing info.
  `high_stakes_flags` are still extracted and audit-logged every turn, but
  no longer route to a different outcome. Phase 2 applies the same
  pattern: a field that keeps failing validation is simply re-asked rather
  than escalated after `MAX_VALIDATION_RETRIES`.
- **Trade-off accepted.** This removes the system's highest-stakes safety
  net — routing prior-refusal/criminal-record/asylum-adjacent cases to a
  human before any determination — in exchange for every user getting a
  real, usable answer instead of a queue that nothing services. Defensible
  *only* because no human is on the other end of that queue in this
  deployment; §5's compliance boundary was narrowed accordingly to rest on
  the Human Review Gate over Phase 2 output, not on advisory-stage
  escalation. A deployment that staffs a caseworker queue should restore
  something closer to the original `ESCALATE` path for high-stakes/
  low-confidence cases — that logic remains in git history
  (`app/workflow/gate.py`, prior to this change).
