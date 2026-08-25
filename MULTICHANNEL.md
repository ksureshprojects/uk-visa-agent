# Plan: WhatsApp + Email integration, cross-channel case identity

**Status:** phases 1-3 below (schema migration, inbound router, OTP linking
flow) are implemented and tested — see `app/storage/models.py`,
`app/workflow/orchestrator.py`, `app/workflow/linking.py`, and
`tests/test_channels.py`. Phases 4-5 (real Twilio WhatsApp/SendGrid
transports, webhook signature verification) are not — routing/linking is
reachable today only via the simulated `POST /channels/{channel}/inbound`
endpoint, standing in for what a real webhook handler would call after
parsing Twilio's payload. Phase 6 (cross-channel LLM context) is done; phase
7 (admin visibility) is partially done — `GET /cases/{id}` shows linked
identities and merged, channel-tagged history.

This document was originally written as a design plan before any of it was
built, and is kept here largely as-written. It extends the architecture in
[ARCHITECTURE.md](ARCHITECTURE.md) — specifically the "real WhatsApp
integration" item listed there as out of scope — to cover two live channels
(WhatsApp, email) plus the new requirement of **one case, reachable from
either channel, with the non-originating channel authenticated by
OTP.**

## 1. Requirements recap

1. Transport: Twilio for both channels (WhatsApp Business API +
   SendGrid/Email API — see §4 on how "consistent" that actually is).
2. Identify the same human across channels — not automatically (we can't
   assume a phone number and an email belong to the same person), but
   **on request**, via verification.
3. A case is owned by the identity of the channel that started it (the
   "originating identity"). A phone number or email address, by itself,
   is *not* an authenticated identity — it's just "whoever controls that
   inbox/number today." That's an important framing point for §6.
4. To reach the case from the *other* channel: give the case reference,
   receive an OTP on the *originating* channel, enter it back. Only then
   is the second channel linked to the case.
5. Once linked, the user can carry on the same case conversation from
   either channel.

## 2. Why "Conversation" can't stay the aggregate root

Today `Conversation` (`app/storage/models.py`) *is* the case: it holds
`visa_type`, `status`, `clarify_rounds_used`, and owns the assessments,
fields, and escalations. It's keyed 1:1 to a single `external_user_id`
from a single channel. That model is correct for "one person, one
channel, one thread" and breaks the moment a second channel needs to
attach to the same case.

So the plan splits today's `Conversation` into two layers:

- **`Case`** — the aggregate root. Everything that is currently on
  `Conversation` (`status`, `visa_type`, `clarify_rounds_used`,
  `assessments`, `fields`, `escalations`, `audit_entries`) moves here,
  unchanged in behavior. This is what the checkpoint gate, assembly
  engine, and escalation rules operate on — none of that logic changes.
- **`Identity`** — a channel-scoped address: `(channel, address)`, e.g.
  `(whatsapp, +447...)` or `(email, jane@example.com)`. Not a person
  record — just a normalized, deduped handle we've seen traffic from.
- **`CaseIdentityLink`** — join table: which identities may act on which
  case, with a `role` of `originating` or `linked`, and `linked_at`.
- **`Conversation`** (redefined, narrower) — a per-channel *thread*:
  `(case_id, identity_id)`, owning the `Message` rows for that channel.
  A case with both channels linked has two `Conversation` rows, one per
  channel, both pointing at the same `Case`. This keeps per-channel
  message formatting/history separate (WhatsApp and email have very
  different message shapes) while the workflow state is shared.

```
Identity (whatsapp, +44...)  ──┐
                                ├──► CaseIdentityLink ──► Case ──► Assessment, ApplicationField,
Identity (email, jane@...)   ──┘         (role: originating/linked)     EscalationRecord, AuditLogEntry
                                                │
                          Conversation(case, identity=whatsapp) ── Message*
                          Conversation(case, identity=email)    ── Message*
```

This is an additive schema change (new tables + a migration of existing
`Conversation` rows into `Case` + a same-id `Conversation` thread +
`Identity` + originating `CaseIdentityLink`), not a rewrite — the
orchestrator, gate, assembly engine, and escalation logic are untouched
because they already only ever touch `case_id`-scoped state.

## 3. Case reference

Add a short, human-typeable reference distinct from the internal UUID —
someone needs to read this off a confirmation message and type it back
on WhatsApp or in an email. Format: `VISA-XXXXX`, 5 base32 chars
(Crockford alphabet — no `0/O/1/I` confusion), generated at case
creation, unique, stored on `Case`. Shown to the user once the case is
opened ("Your case reference is VISA-7F3K9 — keep it if you'd like to
continue this conversation over email too").

## 4. Twilio channel integration

**WhatsApp**: Twilio Programmable Messaging (WhatsApp Business
Solution). Inbound messages arrive as webhook POSTs to our endpoint;
outbound replies go via the Messages API. Needs a Meta-approved sender
and, for anything outside the 24h customer-service session window,
approved message templates (this is the one piece of unavoidable
external lead time — flag it to the user early since template approval
can take days).

**Email**: Twilio's WhatsApp/SMS "consistent API" story does not
actually extend to email the same way — Twilio's email product is
**SendGrid** (same account, separate API surface). Inbound is SendGrid
**Inbound Parse** (configure an MX record, SendGrid POSTs parsed emails
to our webhook); outbound is the SendGrid **Mail Send API**. Worth
being explicit with the user about this: the "one vendor" benefit is
real (one Twilio account, one bill, one support relationship) but the
"one API shape" benefit is not — WhatsApp and email are different
payloads, different auth headers, different reply semantics (thread by
`In-Reply-To`/subject vs. WhatsApp's session/template rules).

That's fine, because `app/api/transport.py`'s `ChatTransport`
abstraction already exists for exactly this reason:
`parse_inbound(payload) -> (ChannelType, address, text)` /
`format_outbound(text) -> payload` — implemented as of phase 1, currently
only by `WebChatTransport` (`ChannelType.WEB`). Plan is two new
implementations:

- `WhatsAppTransport` — parses Twilio's WhatsApp webhook form fields
  (`From`, `Body`, `MessageSid`), validates the Twilio request signature
  (`X-Twilio-Signature` + auth token — **required**, this is the only
  thing standing between us and spoofed inbound webhooks), returns
  `(ChannelType.WHATSAPP, "+44...", text)`.
- `EmailTransport` — parses SendGrid Inbound Parse's multipart payload
  (`from`, `subject`, `text`), strips quoted reply chains, returns
  `(ChannelType.EMAIL, "jane@example.com", text)`; `format_outbound`
  builds a Mail Send API payload, keeping the case reference in the
  subject line (`Re: Your visa case [VISA-7F3K9]`) so a reply keeps the
  ref visible.

Either transport's output feeds `Orchestrator.route_inbound(db, channel,
address, text)` (implemented — §5-6), which is what a real Twilio webhook
handler would call once the payload is parsed down to that shape; today
it's reachable via the simulated `POST /channels/{channel}/inbound`
endpoint instead.

One open question worth flagging to Twilio product docs before
committing to the transport implementation: Twilio **Conversations API**
has, at various points, added email as a participant type behind the
same Conversations abstraction. If that coverage is solid today it would
let Twilio's own layer absorb some of the plumbing above. I'd treat
that as a build-vs-integrate spike (§9), not a blocking assumption —
either way `ChatTransport` isolates the orchestrator from the answer.

`channel` + `address` are stored separately on `Identity` (implemented)
rather than as a single prefixed string, so the same raw phone
number/address can't collide across channels or with the web-chat stub,
and each `Identity` row self-describes its channel.

## 5. Message routing (replaces today's single `start_conversation`)

Every inbound webhook resolves to a channel + raw address, then:

```
normalize address → find-or-create Identity(channel, address)
    │
    ├─ Identity already linked to exactly one open Case
    │     → route message into that Case's Conversation thread (existing flow)
    │
    ├─ Identity linked to more than one open Case
    │     → ask which case (by reference) before routing
    │       [only reachable if we later allow >1 case per identity — see §8]
    │
    ├─ Identity not linked to any Case, message matches /VISA-[A-Z0-9]{5}/
    │     → treat as a cross-channel link attempt → §6 OTP flow
    │
    └─ Identity not linked to any Case, no case reference in message
          → start_conversation: create new Case + originating CaseIdentityLink
            (this is today's behavior, unchanged)
```

The "does this look like a case reference" check is a regex, not an
LLM call — consistent with the repo's principle that routing/gating
decisions are deterministic, not model judgment.

## 6. Cross-channel linking (the OTP flow)

New table `CaseLinkVerification`: `id, case_id, requesting_identity_id,
target_identity_id, code_hash, expires_at, attempt_count, consumed_at,
created_at`.

Sequence, e.g. case opened over WhatsApp, user now emails in:

1. User emails "VISA-7F3K9" (or "I'd like to continue my case
   VISA-7F3K9 by email").
2. Router (§5) finds `Case` by reference. If not found → generic "we
   couldn't find that case reference" (never reveal *why* — see §7).
3. Look up the case's `originating` identity (or, if already
   multi-linked, all currently-linked identities — send to all, so a
   lost phone doesn't lock someone out, but see §7 for the rate-limit
   implication of that).
4. Generate a 6-digit numeric OTP, store only its hash, `expires_at =
   now + 10 min`, `attempt_count = 0`. Send it via that identity's
   channel: "Someone requested to link your visa case VISA-7F3K9 to a
   new email/WhatsApp number. Your verification code is 483920. If
   this wasn't you, ignore this message."
5. Reply to the *requesting* channel: "I've sent a 6-digit code to the
   [WhatsApp number / email] this case started on. Reply with that
   code to continue here."
6. User replies with the code on the requesting channel. Compare
   against the hash; on match and not expired and attempts < 5 →
   create `CaseIdentityLink(case, requesting_identity, role=linked)`,
   create the `Conversation` thread for that (case, identity), mark the
   verification `consumed_at`, and drop the user straight into the case
   ("You're verified — you're at the document collection step,
   picking up where you left off.").
   On mismatch → increment `attempt_count`, generic "that code didn't
   match, try again" (no distinction between wrong/expired — see §7).
7. Every attempt (send + each check) is an `AuditLogEntry` on the case,
   same as every other state transition in this codebase — a caseworker
   reviewing the case sees exactly when/how a second channel was added.

## 7. Security considerations

- **OTP is short-lived and single-use**: 10 min expiry, consumed on
  first correct use, max 5 attempts then the verification record is
  dead (user must re-trigger from §6 step 1, itself rate-limited —
  e.g. max 3 link attempts per case per hour — to stop OTP brute-forcing
  by spamming new codes).
- **Never confirm or deny in a way that leaks case existence**: "case
  reference not found" and "case found but not eligible to link" should
  look identical externally — otherwise an attacker can enumerate valid
  `VISA-XXXXX` codes. Reference space (5 base32 chars ≈ 33M combinations)
  plus rate-limiting per requesting identity is the real defense here,
  not the error message wording alone.
- **The OTP is sent to the *existing* verified channel, never to the
  requesting one** — that's the entire mechanism; a compromised case
  reference alone (which is not really a secret — it's meant to be
  read over the phone to a caseworker) is useless without also
  controlling the original phone/email.
- **Twilio webhook authenticity**: validate `X-Twilio-Signature` on
  every inbound WhatsApp webhook (Twilio's documented HMAC scheme
  against the exact URL + POST params); validate SendGrid Inbound
  Parse similarly (shared secret in the parse URL path, since Inbound
  Parse doesn't sign payloads — this is a known SendGrid limitation,
  worth designing around rather than around discovering later).
- **PII**: phone numbers and email addresses are personal data. Existing
  `AuditLogEntry.payload` is a JSON blob that currently stores whatever
  the LLM saw — needs a pass to make sure raw identity strings aren't
  duplicated all over audit payloads beyond what's needed; the `Identity`
  table becomes the single place that data lives.
- **A verified WhatsApp/email identity is still not a legal identity
  check** — this only proves "controls the channel," same trust level
  the case already implicitly had from its originating channel. Doesn't
  change the OISC/human-review boundary in ARCHITECTURE.md §5.

## 8. Agent/UX consequences

- The advisory/assembly agents currently read a single conversation's
  message history (`app/agents/advisory.py`, `app/workflow/assembly.py`).
  Once a case can have two `Conversation` threads, the LLM context needs
  to be built from **all threads under the case**, ordered by
  `created_at`, not one thread — otherwise the agent "forgets" what was
  said on the other channel. Interleave with a small channel tag per
  message (`[WhatsApp] ...`, `[Email] ...`) so the model doesn't lose
  track of tone/formatting differences (email replies can be longer;
  WhatsApp should stay to a few short messages).
- Reply formatting differs per channel even for the same underlying
  agent output: WhatsApp favors short messages, no attachments-in-body;
  email can carry the fuller draft-package text and, later, actual file
  attachments for the draft package. This stays inside
  `format_outbound`, not the orchestrator.
- Scope decision to make explicitly (not implicitly default): **one
  open case per identity, or many?** Simplest and recommended for v1:
  one *open* (non-`COMPLETED`) case per identity — an identity already
  linked to an open case that tries to start a new one gets routed back
  into the existing case instead of silently forking a second one. This
  avoids the "which case did you mean" disambiguation in §5 entirely
  for v1; multi-case-per-identity is a natural v2 once the reference
  system exists anyway.

## 9. Build plan (phased)

1. ✅ **Schema migration**: introduced `Case`, `Identity`,
   `CaseIdentityLink`, `CaseLinkVerification`; split the old `Conversation`
   into `Case` (workflow state) + redefined `Conversation` (per-channel
   thread). No pre-existing data to backfill (this repo has no production
   database). `app/storage/repository.py` got the new lookup functions
   (`find_or_create_identity`, `get_open_case_for_identity`,
   `create_link_verification`, `verify_link_code`, ...). Gate/assembly
   logic unchanged in behavior — repointed onto `case_id` for workflow
   state and `conversation_id` (now "thread id") for messages only.
2. ✅ **Router**: `Orchestrator.route_inbound` implements §5's
   find-or-create-identity → route/start/link dispatch, using the case
   reference and OTP regexes from `app/workflow/linking.py`.
3. ✅ **OTP linking flow**: `CaseLinkVerification` + the send/verify
   logic from §6 (`Orchestrator._start_link` / `_complete_link`), with
   OTP delivery via an injectable `otp_sender` callable rather than
   `ChatTransport.format_outbound` directly — decouples "who to notify
   and with what code" from "how a real channel sends it," and is what
   `tests/test_channels.py` substitutes with a recording fake.
4. **WhatsApp transport** (not started): `WhatsAppTransport` + Twilio
   webhook endpoint + signature verification + a `FakeWhatsAppClient`
   (mirrors the existing `tests/fake_llm.py` pattern) for offline tests.
5. **Email transport** (not started): `EmailTransport` + SendGrid Inbound
   Parse endpoint + Mail Send integration + a fake client, same pattern.
6. ✅ **Cross-channel context assembly** in the agents (§8) —
   `repository.get_case_history` merges all threads and
   `AdvisoryAgent._format_history_for_llm` tags turns by channel once a
   case has more than one thread.
7. 🟡 **Admin visibility**: `GET /cases/{id}` now returns linked
   identities (with role) and the merged, channel-tagged transcript.
   Still open: audit log entries for `link_otp_sent`/`identity_linked`
   are written (`/admin/cases/{id}/audit`) but not yet surfaced in a
   dedicated "how did this case get here" view.

Each phase should land independently testable against fakes, matching
how `tests/` already isolates the gate/validators/orchestrator from the
real Anthropic API — the same discipline applies to Twilio: nothing in
`app/workflow` or `app/agents` should import a Twilio SDK type directly.

## 10. Open questions for you

- **WhatsApp session/template approval**: do you have (or want to
  start) a Meta Business/WhatsApp Business Platform application now,
  since that approval lead time is the real critical path, independent
  of any code here?
- **One open case per identity (§8) vs. allow many with
  disambiguation** — recommend one for v1; confirm.
- **Should OTP go to *all* linked identities or just the originating
  one**, once a case has 2+ linked channels and a third channel tries
  to join? Recommend: all currently-linked identities, so losing the
  original phone doesn't permanently lock the case, but this needs the
  stricter per-case rate-limit noted in §7.
- **Email formatting**: plain text only for v1, or HTML replies from
  the start (nicer, but widens SendGrid template work)?
