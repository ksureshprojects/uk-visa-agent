"""State machine driving identity verification and case selection for
inbound channel messages (WhatsApp or email).

This module owns *who is talking* (email-OTP verified identity) and *which
case they mean*; once a case is ACTIVE, every further message on it is
handed to app/workflow/orchestrator.py's visa advisory pipeline
(_handle_active), which owns the actual advice. Each Case lazily gets its
own Conversation the first time it goes ACTIVE (Case.conversation_id).

The transition into ACTIVE (_activate_case) also hands over
session.initial_message — the free-form message that started the session,
e.g. "I want to apply for a student visa" — as that Conversation's first
turn. So the moment a case is determined, the reply is the advisory
pipeline's actual answer to what the user already said, not a generic "how
can I help?".

Whenever the advisory pipeline's response includes a completed Phase 2
"package" (app/workflow/assembly.py's build_package output), this module
also emails the user a plain-text field-value summary + document checklist
(_send_package_summary_email) — always via email, even on a WhatsApp
session, since that's the one address every user has already verified.

One state machine step per inbound message, driven by UserSession.state.

WhatsApp sessions go through email-OTP verification before case selection,
since a phone number alone doesn't identify who's messaging:

    (session created) --[any message]--> AWAITING_EMAIL
    AWAITING_EMAIL --[valid email]--> AWAITING_OTP
    AWAITING_OTP --[correct code]--> AWAITING_CASE_CHOICE
    AWAITING_OTP --[expired/locked]--> AWAITING_EMAIL
    AWAITING_CASE_CHOICE --["new"]--> ACTIVE (new case created)
    AWAITING_CASE_CHOICE --["existing", case found]--> AWAITING_EXISTING_CASE_CONFIRM
    AWAITING_CASE_CHOICE --["existing", none found]--> ACTIVE (new case created)
    AWAITING_EXISTING_CASE_CONFIRM --["yes"]--> ACTIVE
    AWAITING_EXISTING_CASE_CONFIRM --["no"]--> AWAITING_CASE_REFERENCE
    AWAITING_CASE_REFERENCE --[known case id]--> ACTIVE
    AWAITING_CASE_REFERENCE --["new"]--> ACTIVE (new case created)
    ACTIVE --["new", current case already complete]--> ACTIVE (new case created)
    ACTIVE --[any other message]--> ACTIVE (logged against the current case)

Email sessions skip identity verification entirely — the sender's From
address already proves they control that inbox, which is the same proof an
OTP-to-email would establish anyway. An email session can therefore only
ever touch cases owned by its own sender address:

    (session created) --[any message]--> user/cases resolved from sender address
        --[no cases]--> ACTIVE (new case created)
        --[1-5 cases]--> AWAITING_CASE_SELECTION
    AWAITING_CASE_SELECTION --[number or case id from the list]--> ACTIVE
"""

import logging
import re

from sqlalchemy.orm import Session as DBSession

from app.identity import case_locks
from app.messaging import gmail, package_summary, twilio_client
from app.storage import identity_repository as repo
from app.storage import repository as workflow_repository
from app.storage.models import ChannelType, ConversationStatus, MessageDirection, SessionState, User, UserSession
from app.workflow.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NEW_CASE_WORDS = {"new"}
_EXISTING_CASE_WORDS = {"existing", "resume", "continue", "old", "previous"}
_YES_WORDS = {"yes", "y", "yeah", "yep", "correct", "confirm", "right", "sure"}
_NO_WORDS = {"no", "n", "nope", "incorrect", "wrong"}


def _matches_any(text: str, words: set[str]) -> bool:
    tokens = set(text.lower().split())
    return bool(tokens & words) or text.lower() in words


class IdentitySessionManager:
    """Entry point for both webhook handlers. `handle_inbound_message`
    takes a raw (channel_type, channel_identifier, text) triple, advances
    the session state machine by one step, and sends + logs the reply
    itself so callers don't need to know how a given channel is dispatched.

    `orchestrator` is None at import time (the LLM/KB-backed Orchestrator
    can't be built until app startup) and is set once by
    app/api/main.py:startup() on the shared `manager` instance below.
    """

    def __init__(self, orchestrator: Orchestrator | None = None):
        self.orchestrator = orchestrator

    def handle_inbound_message(
        self, db: DBSession, channel_type: ChannelType, channel_identifier: str, text: str
    ) -> dict:
        session, is_new = repo.get_or_create_active_session(db, channel_type, channel_identifier)
        logger.info(
            "session=%s channel=%s identifier=%s new_session=%s state=%s user=%s case=%s: inbound %r",
            session.id, channel_type.value, channel_identifier, is_new,
            session.state.value, session.user_id, session.case_id, text,
        )
        repo.touch_activity(db, session)
        repo.log_message(
            db, session.id, session.case_id, MessageDirection.INBOUND,
            channel_type, channel_identifier, text,
        )

        # A session already tied to a case could be advanced from either
        # channel at the same moment (e.g. the email poller's background
        # thread and a WhatsApp webhook thread both landing on the same
        # case) — serialize the turn per case_id so the same case is never
        # processed by two threads at once. Sessions not yet tied to a case
        # (still in identity verification / case selection) aren't touching
        # shared case state, so they proceed without a lock; different
        # cases each get their own lock and still run fully in parallel.
        lock = case_locks.lock_for_case(session.case_id) if session.case_id else None
        if lock is not None:
            with lock:
                reply = self._generate_reply(db, session, channel_type, is_new, text)
        else:
            reply = self._generate_reply(db, session, channel_type, is_new, text)

        self._reply(db, session, reply)
        logger.info("session=%s state=%s: outbound %r", session.id, session.state.value, reply)
        return {"session_id": session.id, "state": session.state.value, "reply": reply}

    def _generate_reply(
        self, db: DBSession, session: UserSession, channel_type: ChannelType, is_new: bool, text: str
    ) -> str:
        if is_new:
            repo.set_initial_message(db, session, text)
            if channel_type == ChannelType.EMAIL:
                return self._start_email_session(db, session)
            return (
                "Welcome! Before we get started, could you tell me the email address "
                "you'd like to use to verify your identity?"
            )
        return self._advance(db, session, text.strip())

    def _advance(self, db: DBSession, session: UserSession, text: str) -> str:
        handler = getattr(self, f"_handle_{session.state.value}", None)
        if handler is None:
            logger.error("session=%s: no handler for state=%s", session.id, session.state.value)
            return "Sorry, something went wrong on our end. Please try again shortly."
        logger.debug("session=%s: dispatching to %s", session.id, handler.__name__)
        return handler(db, session, text)

    # --- email channel: identity is the sender address, no OTP needed ------

    def _start_email_session(self, db: DBSession, session: UserSession) -> str:
        email_address = session.channel_identifier.strip().lower()
        user = repo.find_or_create_user(db, email_address)
        repo.link_user(db, session, user)
        logger.info(
            "session=%s: email channel auto-verified user=%s (%s)",
            session.id, user.id, email_address,
        )
        return self._present_case_choices(db, session, user)

    def _present_case_choices(self, db: DBSession, session: UserSession, user: User) -> str:
        cases = repo.list_recent_cases(db, user.id, limit=5)

        if not cases:
            case = repo.create_case(db, user.id, summary=session.initial_message)
            ack = (
                f"Thanks — I couldn't find any existing cases for your email address, "
                f"so I've started a new one: {case.id}."
            )
            return self._activate_case(db, session, case.id, ack)

        repo.set_pending_case_choices(db, session, [case.id for case in cases])
        repo.set_state(db, session, SessionState.AWAITING_CASE_SELECTION)
        logger.info(
            "session=%s user=%s: presenting %d existing case(s) for selection",
            session.id, user.id, len(cases),
        )
        lines = [
            f"{i}. {case.id} — {case.summary or '(no summary recorded)'}"
            for i, case in enumerate(cases, start=1)
        ]
        return (
            "Welcome back! I found existing cases associated with your email address:\n"
            + "\n".join(lines)
            + "\n\nWhich one would you like to continue discussing? Reply with the number or case id."
        )

    # --- state handlers ----------------------------------------------------

    def _handle_awaiting_email(self, db: DBSession, session: UserSession, text: str) -> str:
        email = text.strip().lower()
        if not _EMAIL_RE.match(email):
            logger.info("session=%s: rejected invalid email format %r", session.id, text)
            return "That doesn't look like a valid email address. Could you send it again?"

        repo.store_pending_email(db, session, email)
        code = repo.issue_otp(db, session)
        otp_body = f"Your verification code is {code}. It expires in a few minutes."
        gmail.send_email(to=email, subject="Your verification code", body=otp_body)
        repo.log_message(
            db, session.id, session.case_id, MessageDirection.OUTBOUND,
            ChannelType.EMAIL, email, otp_body,
        )

        repo.set_state(db, session, SessionState.AWAITING_OTP)
        logger.info("session=%s: OTP issued to %s, state -> AWAITING_OTP", session.id, email)
        return f"We've sent a verification code to {email}. Please enter it here to continue."

    def _handle_awaiting_otp(self, db: DBSession, session: UserSession, text: str) -> str:
        result = repo.verify_otp(db, session, text)
        logger.info("session=%s: OTP verification result=%s", session.id, result)

        if result == "verified":
            user = repo.find_or_create_user(db, session.pending_email)
            repo.link_user(db, session, user)
            repo.set_state(db, session, SessionState.AWAITING_CASE_CHOICE)
            logger.info(
                "session=%s: verified user=%s (%s), state -> AWAITING_CASE_CHOICE",
                session.id, user.id, session.pending_email,
            )
            return (
                f'Thanks, you\'re verified. Picking up your earlier message: "{session.initial_message}". '
                "Would you like to start a new case, or continue an existing case?"
            )

        if result in ("expired", "locked"):
            repo.clear_verification(db, session)
            repo.set_state(db, session, SessionState.AWAITING_EMAIL)
            reason = "expired" if result == "expired" else "had too many incorrect attempts"
            logger.info("session=%s: OTP %s, state -> AWAITING_EMAIL", session.id, result)
            return f"That verification code {reason}. Let's start again — what's your email address?"

        return "That code didn't match. Please double-check and enter it again."

    def _handle_awaiting_case_choice(self, db: DBSession, session: UserSession, text: str) -> str:
        if _matches_any(text, _NEW_CASE_WORDS):
            case = repo.create_case(db, session.user_id, summary=session.initial_message)
            return self._activate_case(db, session, case.id, f"Started new case {case.id}.")

        if _matches_any(text, _EXISTING_CASE_WORDS):
            recent = repo.get_most_recent_case(db, session.user_id)
            if recent is None:
                case = repo.create_case(db, session.user_id, summary=session.initial_message)
                logger.info(
                    "session=%s user=%s: no existing cases found, starting new case=%s",
                    session.id, session.user_id, case.id,
                )
                return self._activate_case(
                    db, session, case.id,
                    f"I couldn't find any existing cases for you, so I've started a new one: {case.id}.",
                )
            repo.set_pending_case(db, session, recent.id)
            repo.set_state(db, session, SessionState.AWAITING_EXISTING_CASE_CONFIRM)
            logger.info(
                "session=%s user=%s: found recent case=%s, state -> AWAITING_EXISTING_CASE_CONFIRM",
                session.id, session.user_id, recent.id,
            )
            summary = recent.summary or "(no summary recorded)"
            return (
                f'Your most recent case is {recent.id}: "{summary}". '
                "Is that the case you'd like to continue? (yes/no)"
            )

        return (
            "Sorry, I didn't catch that — would you like to start a new case, "
            "or continue an existing case?"
        )

    def _handle_awaiting_existing_case_confirm(
        self, db: DBSession, session: UserSession, text: str
    ) -> str:
        if _matches_any(text, _YES_WORDS):
            case_id = session.pending_case_id
            repo.set_pending_case(db, session, None)
            return self._activate_case(db, session, case_id, f"Great, continuing case {case_id}.")

        if _matches_any(text, _NO_WORDS):
            repo.set_pending_case(db, session, None)
            repo.set_state(db, session, SessionState.AWAITING_CASE_REFERENCE)
            logger.info(
                "session=%s user=%s: declined suggested case, state -> AWAITING_CASE_REFERENCE",
                session.id, session.user_id,
            )
            return "No problem — could you provide the case reference id you'd like to continue?"

        return "Sorry, could you confirm with yes or no — is that the right case?"

    def _handle_awaiting_case_reference(self, db: DBSession, session: UserSession, text: str) -> str:
        if _matches_any(text, _NEW_CASE_WORDS):
            case = repo.create_case(db, session.user_id, summary=session.initial_message)
            return self._activate_case(db, session, case.id, f"Started new case {case.id}.")

        case_id = text.strip()
        case = repo.get_case(db, case_id)
        if case is None or case.user_id != session.user_id:
            logger.info(
                "session=%s user=%s: case reference %r not found or not owned by user",
                session.id, session.user_id, case_id,
            )
            return (
                "I couldn't find a case with that reference id for your account. "
                "Could you check and send it again?"
            )
        return self._activate_case(db, session, case.id, f"Thanks, continuing case {case.id}.")

    def _handle_awaiting_case_selection(self, db: DBSession, session: UserSession, text: str) -> str:
        choice_ids = [c for c in (session.pending_case_ids or "").split(",") if c]
        stripped = text.strip()

        selected_id = None
        if stripped.isdigit():
            index = int(stripped) - 1
            if 0 <= index < len(choice_ids):
                selected_id = choice_ids[index]
        elif stripped in choice_ids:
            selected_id = stripped

        if selected_id is None:
            logger.info(
                "session=%s user=%s: unrecognized case selection %r (options=%s)",
                session.id, session.user_id, text, choice_ids,
            )
            return (
                "Sorry, I didn't recognize that choice. Please reply with the number "
                "or case id from the list above."
            )

        # Options only ever come from list_recent_cases(user_id=...), but
        # re-check ownership since it's cheap and this is identity-sensitive.
        case = repo.get_case(db, selected_id)
        if case is None or case.user_id != session.user_id:
            logger.error(
                "session=%s user=%s: selected case=%s missing or not owned",
                session.id, session.user_id, selected_id,
            )
            return "Sorry, something went wrong finding that case. Could you try again?"

        repo.set_pending_case_choices(db, session, [])
        return self._activate_case(db, session, case.id, f"Great, continuing case {case.id}.")

    def _handle_active(self, db: DBSession, session: UserSession, text: str) -> str:
        if self.orchestrator is None:
            logger.error(
                "session=%s user=%s case=%s: orchestrator not wired yet, cannot route to advisory agent",
                session.id, session.user_id, session.case_id,
            )
            return "Sorry, the assistant isn't ready yet — please try again in a moment."

        # Only treat "new" as a start-a-new-case command once the current
        # case is actually finished — otherwise a mid-conversation message
        # that happens to contain "new" (e.g. "I have a new employer") would
        # hijack an in-progress case instead of being answered normally.
        if _matches_any(text, _NEW_CASE_WORDS) and self._case_is_complete(db, session.case_id):
            return self._start_new_case(db, session, text)

        result = self._route_to_advisory(db, session, session.case_id, text)
        return result["reply_to_user"]

    def _case_is_complete(self, db: DBSession, case_id: str) -> bool:
        case = repo.get_case(db, case_id)
        if case is None or case.conversation_id is None:
            return False
        convo = workflow_repository.get_conversation(db, case.conversation_id)
        return convo is not None and convo.status in (
            ConversationStatus.READY_FOR_HUMAN_REVIEW, ConversationStatus.COMPLETED,
        )

    def _start_new_case(self, db: DBSession, session: UserSession, text: str) -> str:
        case = repo.create_case(db, session.user_id, summary=text)
        repo.link_case(db, session, case.id)
        logger.info(
            "session=%s user=%s: previous case=%s complete, started new case=%s",
            session.id, session.user_id, session.case_id, case.id,
        )
        result = self._route_to_advisory(db, session, case.id, text)
        return f"Started new case {case.id}.\n\n{result['reply_to_user']}"

    def _activate_case(self, db: DBSession, session: UserSession, case_id: str, ack: str) -> str:
        """Move `session` onto `case_id` and, instead of a bare "how can I
        help" prompt, hand the message that started this session straight to
        the advisory pipeline — so e.g. "I want to apply for a student visa"
        sent as the very first message is acted on immediately rather than
        asked for again. Only session.initial_message is used, not every
        inbound message logged this session: verification-flow replies
        (email address, OTP code, new/existing/yes-no keywords) are
        state-machine answers, not advisory content, and feeding them to the
        LLM would just be noise (and put a one-time OTP code in the audit
        log for no reason)."""
        repo.link_case(db, session, case_id)
        repo.set_state(db, session, SessionState.ACTIVE)
        logger.info("session=%s user=%s: activated case=%s", session.id, session.user_id, case_id)

        if self.orchestrator is None:
            logger.error(
                "session=%s user=%s case=%s: orchestrator not wired yet, cannot route to advisory agent",
                session.id, session.user_id, case_id,
            )
            return f"{ack} Sorry, the assistant isn't ready yet — please try again in a moment."

        result = self._route_to_advisory(db, session, case_id, session.initial_message)
        return f"{ack}\n\n{result['reply_to_user']}"

    def _route_to_advisory(self, db: DBSession, session: UserSession, case_id: str, text: str) -> dict:
        conversation_id = self._ensure_conversation(db, session)
        result = self.orchestrator.handle_message(db, conversation_id, text)
        repo.touch_case(db, case_id, summary=text)
        logger.info(
            "session=%s user=%s case=%s conversation=%s: routed to advisory agent, status=%s",
            session.id, session.user_id, case_id, conversation_id, result.get("status"),
        )
        if result.get("package") is not None:
            emailed_to = self._send_package_summary_email(db, session, case_id, result["package"])
            if emailed_to:
                result["reply_to_user"] += f"\n\nI've also emailed the full package details to {emailed_to}."
        return result

    def _send_package_summary_email(
        self, db: DBSession, session: UserSession, case_id: str, package: dict
    ) -> str | None:
        """Returns the address emailed on success, None otherwise (so the
        caller can only tell the user about the email if it actually sent)."""
        user = repo.get_user(db, session.user_id)
        if user is None:
            logger.error(
                "session=%s case=%s: no user linked to session, cannot email package summary",
                session.id, case_id,
            )
            return None

        subject, body = package_summary.format_package_email(case_id, package)
        try:
            gmail.send_email(to=user.email, subject=subject, body=body)
        except Exception:
            # Best-effort: this is a supplementary notification alongside the
            # primary reply already sent via _reply, not the turn's main
            # deliverable — a failure here shouldn't fail the whole turn.
            logger.exception(
                "session=%s case=%s: failed to email package summary to %s",
                session.id, case_id, user.email,
            )
            return None

        repo.log_message(
            db, session.id, case_id, MessageDirection.OUTBOUND,
            ChannelType.EMAIL, user.email, body,
        )
        logger.info(
            "session=%s case=%s: emailed package summary to %s", session.id, case_id, user.email,
        )
        return user.email

    def _ensure_conversation(self, db: DBSession, session: UserSession) -> str:
        case = repo.get_case(db, session.case_id)
        if case.conversation_id is not None:
            return case.conversation_id

        convo = self.orchestrator.start_conversation(db, external_user_id=session.user_id)
        repo.set_case_conversation(db, case, convo.id)
        logger.info(
            "session=%s user=%s case=%s: created conversation=%s",
            session.id, session.user_id, session.case_id, convo.id,
        )
        return convo.id

    # --- outbound dispatch ---------------------------------------------------

    def _reply(self, db: DBSession, session: UserSession, text: str) -> None:
        logger.debug(
            "session=%s: dispatching reply via %s to %s",
            session.id, session.channel_type.value, session.channel_identifier,
        )
        try:
            if session.channel_type == ChannelType.WHATSAPP:
                twilio_client.send_whatsapp(to=session.channel_identifier, body=text)
            else:
                gmail.send_email(to=session.channel_identifier, subject="UK Visa Agent", body=text)
        except Exception:
            logger.exception(
                "session=%s: failed to send reply via %s to %s",
                session.id, session.channel_type.value, session.channel_identifier,
            )
            raise

        repo.log_message(
            db, session.id, session.case_id, MessageDirection.OUTBOUND,
            session.channel_type, session.channel_identifier, text,
        )


# Single shared instance — both app/api/webhooks.py and
# app/messaging/email_poller.py use this one, so app/api/main.py's
# startup() only has to wire up `orchestrator` in one place.
manager = IdentitySessionManager()
