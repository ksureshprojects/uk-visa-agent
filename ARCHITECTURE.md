# Architecture: UK Visa Service Agent

This document is the primary deliverable for the second half of the brief:
*"how do we make agentic service delivery predictable and production-grade?"*
The code in this repo is a working proof of the design described here. It
started scoped to a single visa category (Standard Visitor Visa) over a
stub web-chat transport so the pipeline could run end-to-end rather than
being broad and half-built; it has since grown to four visa categories and
real WhatsApp/email channels with identity verification, while keeping the
same two-phase pipeline and gate as the reliability backbone. §8 walks
through that evolution step by step for anyone comparing this to an earlier
version of the doc.

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

Inbound messages now arrive over WhatsApp or email, not just the web-chat
stub, so there's a channel/identity layer in front of the two-phase pipeline
that decides *who is messaging and which case they mean* before anything
touches the advisory agent. See §8.5 for why that layer exists and how its
own state machine works.

```
Inbound WhatsApp / Email / web-chat message
    │
    ▼
┌───────────────────────────────┐
│ IdentitySessionManager          │  channel + identity layer
│ (app/identity/session_manager)  │  (see §8.5)
│                                  │
│  Email-OTP verification          │──► email delivery: real, via Gmail
│  (WhatsApp only — email sender   │    SMTP (app/messaging/gmail.py)
│  address is already proof for    │
│  the email channel)              │
│  Case selection (new/existing)   │
│  Per-case lock so a WhatsApp and │
│  email message on the same case  │
│  can't race each other           │
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
│  over curated, versioned      │    versioned markdown chunks with
│  Immigration Rules extracts,  │    citation_id + source_url, now
│  now spanning 4 visa types    │    spanning 4 visa categories,
│                               │    retrieved by semantic embedding
│  Output: structured           │    similarity (see §8.1)
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
                     │  │ CLARIFY: ask a batched  │──┐ loops back to
                     │  │ follow-up question,     │  │ Advisory Agent
                     │  │ bounded by               │  │ for the next
                     │  │ MAX_CLARIFY_ROUNDS (3)   │  │ turn
                     │  └───────────────────────┘◄─┘
                     │        │ rounds exhausted, still not confident
                     │        ▼
                     │  FORCE_PASS: commit to the top candidate anyway,
                     │  reply caveats it as best-effort (see §8.6)
                     ▼        │
              PASS ◄──────────┘
               │
               ▼
┌──────────────────────────┐
│ Visa type has a Phase 2   │──no──► Advisory summary itself IS the
│ assembly schema? (all 4   │        service for this category — the
│ supported categories do)  │        conversation stays open for
└──────────────┬────────────┘        follow-up, no human queue exists
               │ yes                 to route it to instead (see §8.6)
               ▼
┌───────────────────────────┐
│ PHASE 2 — Assembly          │
│ (app/workflow)               │
│ deterministic zone            │
│                                │
│ Finite state machine           │
│ over a JSON schema              │
│ (data/schemas/*.json, one        │
│ per visa type)                    │
│                                     │
│ LLM role: extract up to 7 field     │
│ values per message from NL text     │
│ ONLY (batched)                       │
│                                        │
│ Validator role: format, cross-field,   │
│ document checklist — LLM never marks    │
│ a field "done"; a failing field is just │
│ re-asked (no escalation queue exists,    │
│ see §8.6)                                 │
└──────────────┬─────────────────────────────┘
               │ all required fields + docs pass validation
               ▼
┌─────────────────────────┐
│ HUMAN REVIEW GATE         │  always required before "final" — the one
│ produces a submission-    │  human checkpoint that still exists in this
│ ready DRAFT package, never│  system (this is a deliberate product
│ an auto-submitted one.    │  boundary — see §5). The completed package
│ Emailed to the user as a  │  is also emailed to the user directly
│ plain-text summary, even  │  (app/messaging/package_summary.py), even
│ on a WhatsApp case.       │  if the conversation happened over WhatsApp.
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
(3) follow-up questions — each batching up to 3 related questions at once —
before the gate is forced to commit to a determination rather than keep
asking. Prevents an agent stuck in "just one more question" from stalling
the conversation indefinitely — a concrete, testable reliability property.

**Gate outcomes** (`app/workflow/gate.py`, `evaluate_checkpoint`), all
rule-based — no human queue exists in this deployment, so every path
terminates in either another clarifying question or a determination, never
a dead end:
1. `CLARIFY` — confidence below threshold, missing citations, an unresolved
   contradiction, or two candidate visa types too close to call — and
   clarification rounds remain.
2. `FORCE_PASS` — same conditions as above, but `MAX_CLARIFY_ROUNDS` is
   exhausted: the gate commits to the top candidate anyway rather than
   stalling, and the orchestrator's reply explicitly caveats it as a
   best-effort assessment (`_best_effort_reply`) rather than presenting it
   with full confidence.
3. `PASS` — confidence at/above threshold, every eligibility claim carries a
   citation, no unresolved contradictions, and a clear top candidate.

`high_stakes_flags` (prior refusal, criminal record mention, asylum-adjacent
language) are still extracted by the advisory agent and still logged to
every audit entry for the turn, but no longer force a distinct outcome —
see §8.6 for why, and the honest trade-off that comes with it.

**Deterministic validation, no escalation-out in Phase 2 either.** A field
that repeatedly fails validation is simply re-asked (with a gentler prompt
after `MAX_VALIDATION_RETRIES`), not routed to a human — the retry count is
still tracked per field for the audit trail, it just no longer caps
anything. See §8.6.

**State snapshotting & recovery.** Every checkpoint (phase transition, field
update) persists the full structured state — not just chat history — to the
Conversation Store. A resumed session, or a case reviewed later, picks up
from that structured state rather than replaying the transcript. Each write
is a single row insert (append-only), so a retried step after a timeout does
not corrupt state. The identity/case layer (§8.5) mirrors this pattern with
its own append-only `ChannelMessage` log, independent of the Conversation
Store's message log.

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

Note the scope of that boundary narrowed as the gate evolved (§8.6): the
original design also routed low-confidence/high-stakes *advisory* turns to
a human caseworker before any determination was given. That escalation path
no longer exists — Phase 1 now always resolves to a (possibly best-effort,
clearly caveated) determination. The Human Review Gate on the *Phase 2
output* — nothing is ever auto-submitted — is unchanged and is still the
system's real compliance backstop.

## 6. What's out of scope for this demo (and why that's a scoping decision, not a gap)

- **Auth, deployment, scaling, fine-tuning.** Not the interesting problem
  for this brief; noted so their absence reads as scope control, not
  oversight.
- **A human caseworker queue.** `EscalationRecord` (`app/storage/models.py`)
  and `repository.create_escalation` still exist in the schema, but nothing
  calls the latter anymore — see §8.6 for why the gate stopped escalating
  and what that trades away.
- **Document OCR/verification.** Document checklist items are confirmed by
  the user in chat (presence/description), not OCR-verified — a real
  deployment would plug document processing in at that point in Phase 2's
  state machine.
- **Telegram, or any channel beyond WhatsApp/email/web-chat.** WhatsApp and
  email are now real (§8.2, §8.3, §8.5); a further channel would need its own webhook
  handler plus a `ChannelType` entry, but reuses the entire identity/case/
  advisory/assembly stack unchanged.
- **KB verification against raw gov.uk text.** This environment's network
  egress blocked direct fetches of gov.uk/legislation.gov.uk pages during
  the *original* Standard Visitor curation pass, so that slice of
  `data/kb/` was compiled from cross-checked web-search snippets citing
  official gov.uk URLs rather than the raw page/rule text itself. Flagged
  per-file and in `data/kb/INDEX.md`. The three visa categories added later
  (Skilled Worker, Student, Family (Partner)) carry the same caveat.
  Sufficient to demonstrate the RAG/citation architecture; a production
  system would re-verify against primary sources before relying on it for
  real advice.
- **SendGrid-based email sending.** `app/messaging/twilio_client.send_email`
  is written against SendGrid's Mail Send API but is a stub (returns
  `{"status": "stubbed", ...}` without calling out) pending SendGrid
  credentials. All real email today — OTP codes, replies, package summaries
  — goes out through `app/messaging/gmail.py`'s SMTP client instead;
  see §8.3 and §8.5.

## 7. Repository layout

```
app/
  agents/       Phase 1 advisory agent, prompt construction, VisaAssessment model
  identity/      Identity/case state machine (session_manager.py) + per-case
                 locking (case_locks.py) sitting in front of the pipeline — §8.5
  messaging/     Outbound/inbound channel adapters: gmail.py (real SMTP/IMAP),
                 twilio_client.py (real WhatsApp, stub SendGrid email, §8.3),
                 email_poller.py (IMAP polling + enquiry filtering, §8.2),
                 package_summary.py (Phase 2 package -> email formatting)
  workflow/      Phase 2 state machine, checkpoint gate (no escalation, §8.6)
  kb/            KB loading, chunking, embedding, retrieval — semantic
                 embeddings via fastembed, not TF-IDF (§8.1)
  llm/           LLMProvider interface + Anthropic adapter (+ stub second adapter)
  storage/       SQLAlchemy models: conversations/messages/assessments/audit
                 log (advisory pipeline) + users/cases/sessions/channel
                 messages (identity layer, §8.5)
  api/           FastAPI app, ChatTransport abstraction + web-chat stub
                 endpoint, webhooks.py (Twilio inbound WhatsApp/email)
data/
  kb/            Curated markdown source chunks, now across 4 visa categories
  schemas/       JSON requirement schema per visa type (4 today)
static/          Minimal single-page chat UI
scripts/         start_tunnel.sh — Cloudflare quick tunnel for exposing a
                 local dev server publicly without a static IP or open
                 firewall port (§8.4)
tests/           Checkpoint-gate, validator, state-machine, identity-session,
                 messaging-adapter unit tests + classification golden-set eval
```

## 8. How this evolved since the initial version

The initial commit (`Scaffold WhatsApp-native UK visa agent: two-phase
pipeline demo`) shipped exactly one visa category, one channel (a web-chat
stub), no identity layer, and a gate with an escalate-to-human path for
everything it wasn't confident about. Everything below is what changed on
top of that and, more importantly, *why* — most of these weren't isolated
feature adds, they're the same reliability problem (§1) re-solved once the
system had to actually run against multiple visa types and real,
un-authenticated channels.

### 8.1 Retrieval: TF-IDF → local semantic embeddings

The original `Vectorizer` (`app/kb/embeddings.py`) was hand-rolled TF-IDF
cosine similarity with a stopword list — a deliberate choice at the time to
avoid an embedding API dependency for a KB of a few dozen chunks. It has a
real weakness once the KB stops being one visa type's worth of
hand-matched vocabulary: TF-IDF ranks on literal word overlap, so a user
writing "my girlfriend" against a KB chunk written as "partner" scores low
even though they mean the same eligibility rule.

The fix keeps the "no external API dependency" property but swaps the
matching strategy: `fastembed` runs a small quantized transformer
(`BAAI/bge-small-en-v1.5`) fully on-device via `onnxruntime`, not PyTorch —
still zero network calls at inference time, only a one-off model download
cached to `EMBEDDING_MODEL_CACHE_DIR` (`.model_cache/`, gitignored) so a
restart doesn't re-download it. This ranks by semantic similarity, so
colloquial or synonymous phrasing still retrieves the right chunk. `RETRIEVAL_TOP_K`
was also raised 4 → 6 (`app/config.py`) once the corpus spanned four visa
categories rather than one, so a query has enough headroom to surface the
right category's chunks even when a generic term (e.g. "documents") matches
chunks across several categories.

### 8.2 Email channel: inbound filtering

Once the email channel polls a real mailbox (`app/messaging/email_poller.py`)
rather than a dedicated agent-only address, most unread mail — newsletters,
notifications, unrelated correspondence — is not a case enquiry at all, and
blindly running every unread message through identity verification and the
advisory agent would be both wrong behavior and a wasted LLM call. Two
layers of filtering were added: `gmail.fetch_unread` takes
`text_filter_terms` as a server-side IMAP pre-filter so a large `UNSEEN`
backlog doesn't have every message's full body downloaded just to be
discarded; `_looks_like_visa_enquiry` then re-checks, requiring both "uk"
and "visa" as whole words in the subject or body, as the actual gate. A
message that doesn't pass is logged and left unread rather than silently
consumed, so nothing is lost if the filter is ever too strict.

### 8.3 Twilio integration: real WhatsApp sending

`app/messaging/twilio_client.py` is new: `send_whatsapp` calls Twilio's
Messaging API for real (`TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` /
`TWILIO_WHATSAPP_FROM`), which meant handling a constraint that didn't exist
in the original web-chat-only design — Twilio's WhatsApp API rejects an
over-limit message body outright (error 21617) rather than auto-segmenting
the way plain SMS does, so `_split_message` breaks long replies (a batched
Phase 2 question, or a full advisory determination) into multiple sends,
preferring paragraph then word boundaries so a chunk never cuts mid-word.
`send_email` on this same client is written against Twilio's SendGrid Mail
Send API but is left a stub pending SendGrid credentials — the email
channel's actual sends go through `app/messaging/gmail.py` (SMTP/IMAP)
instead (§8.5), which was simpler to stand up for this demo than getting a
SendGrid sender identity verified.

### 8.4 Cloudflare quick tunnel: a hard-to-guess public URL

Testing the WhatsApp/email webhooks needs a public URL for Twilio/SendGrid
to POST to, but this deployment runs on a machine without a static IP or
any opened firewall port (e.g. a GCE VM). `scripts/start_tunnel.sh` starts
the app on `127.0.0.1` and pipes it through `cloudflared tunnel --url`,
Cloudflare's free "quick tunnel" — an outbound-only connection from the VM
to Cloudflare's edge, so no inbound firewall rule is ever needed. The
tradeoff is explicit and documented in the script itself: since the app has
no auth, the resulting `*.trycloudflare.com` URL is an open door to anyone
who has it. The **only** mitigation is that a quick tunnel's hostname is a
randomly generated, unguessable subdomain that isn't published or indexed
anywhere — not a substitute for real auth, just enough obscurity for
throwaway manual testing, and the script's own comments say so.

### 8.5 Identity verification: the email-OTP workflow

The original design had no notion of "who is this," since the only channel
was a stub that took a caller-supplied `external_user_id` directly. Real
channels can't do that: a WhatsApp phone number or an inbound email address
is an identifier, not proof of identity, and a case-preparation assistant
handling immigration details can't let a phone number alone pull up
somebody else's case. `app/identity/session_manager.py` (`IdentitySessionManager`)
adds a state machine (`UserSession.state`) in front of the advisory
pipeline:

- **WhatsApp** goes through email-OTP verification, since a phone number
  alone doesn't identify who's messaging: `AWAITING_EMAIL` →
  (`gmail.send_email` sends a 6-digit code, `OTP_LENGTH`/`OTP_EXPIRY_MINUTES`/
  `OTP_MAX_ATTEMPTS` in `app/config.py`) → `AWAITING_OTP` → (correct code)
  `AWAITING_CASE_CHOICE`. An expired code or too many wrong attempts loops
  back to `AWAITING_EMAIL` rather than locking the user out permanently.
- **Email** skips OTP entirely — the sender's `From` address already proves
  they control that inbox, which is the same proof an OTP-to-email would
  establish anyway, so an email session goes straight to case selection
  scoped to that sender's own cases.
- Both paths converge on the same case-selection sub-machine (new case /
  resume most recent / reference an existing case id by hand, or pick from
  a numbered list for email's multi-case case), and once a case is
  `ACTIVE`, `_activate_case` hands the message that started the session
  straight to the advisory pipeline rather than a generic "how can I help,"
  so answering a verification prompt never throws away what the user
  originally said.

This is also why a `User`/`Case`/`UserSession`/`ChannelMessage` model group
(`app/storage/models.py`) sits alongside, not merged into, the original
`Conversation`/`Message` models: the two model groups answer different
questions — *who is messaging and which case* vs. *what has the advisory
pipeline decided for this specific case's conversation* — and a `Case`
lazily gets its own `Conversation` only once it goes `ACTIVE`. Since the
email poller (background thread) and a WhatsApp webhook (request-pool
thread) can now both be mid-processing at the same instant, `app/identity/case_locks.py`
adds a per-case lock so two channels landing on the same case can't race
each other into duplicate answers or double-incremented clarify rounds —
different cases still process fully in parallel.

### 8.6 Case/gate state-transition iteration: escalation → always-a-determination

This is the largest behavioral change, and it's a genuine trade-off, not a
pure improvement — worth stating plainly rather than glossing over.

The original gate (`GateDecision.ESCALATE`) routed low-confidence
determinations, unresolved contradictions, ambiguous visa types, and
**any** high-stakes flag (prior refusal, criminal record mention,
asylum-adjacent language) — unconditionally, regardless of confidence — to
`ConversationStatus.NEEDS_HUMAN_REVIEW`, on the reasoning that the cost of a
wrong autonomous answer is highest exactly there. That model assumed a
caseworker queue actually gets worked. This deployment doesn't have one —
there's no caseworker UI, no notification, nothing consuming
`EscalationRecord` rows — so under the original gate, an escalated
conversation was a dead end from the user's point of view: told "a
caseworker will follow up," with no caseworker who ever would.

The gate was rewritten (`app/workflow/gate.py`) to never produce a dead
end: `GateDecision` is now only `CLARIFY` or `PASS`/`FORCE_PASS`.
`CLARIFY` behaves as before, bounded by `MAX_CLARIFY_ROUNDS`. Once rounds
are exhausted without a confident, cited, uncontradicted, unambiguous
determination, the gate now **force-passes**: it commits to the top
candidate and the orchestrator's reply (`_best_effort_reply` in
`app/workflow/orchestrator.py`) explicitly caveats it as best-effort,
surfacing any contradictions or missing info it's aware of rather than
presenting it as certain. `high_stakes_flags` are still extracted and still
logged on every audit entry for the turn (so the information isn't lost —
it's still visible to whoever reviews the audit trail later), but no longer
force a different code path; a high-stakes case now gets the same
confidence-gated treatment as any other. The same pattern was applied in
Phase 2: a field that keeps failing validation is simply re-asked, forever
if need be, instead of escalating after `MAX_VALIDATION_RETRIES`.

The honest framing: this trades "some users get told a human is coming (but
none actually do)" for "every user gets a real, usable answer, but the
system's own worst-case safety net — routing exactly the highest-stakes
situations to a human — no longer exists." That's a defensible trade *for a
system with no human on the other end of the queue*, and it's why §5's
compliance boundary was narrowed to lean entirely on the Human Review Gate
over the Phase 2 *output* (never auto-submitted) rather than on an advisory-
stage escalation that had no one to answer it. A production deployment that
actually staffs a caseworker queue should restore something closer to the
original `ESCALATE` path for the high-stakes and low-confidence cases —
the code for that reasoning still exists in git history
(`app/workflow/gate.py` prior to this change) if it's needed again.
