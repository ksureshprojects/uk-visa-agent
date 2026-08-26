"""State machine driving identity verification and case selection for
inbound channel messages (WhatsApp or email).

This is intentionally decoupled from app/workflow/orchestrator.py's visa
advisory pipeline: this module owns *who is talking* (email-OTP verified
identity) and *which case they mean*, before a message would ever reach
the advisory agent. Wiring a verified, case-attributed message into that
pipeline is a follow-up, not part of this slice.

One state machine step per inbound message, driven by UserSession.state.

WhatsApp sessions go through email-OTP verification before case selection,
since a phone number alone doesn't identify who's messaging:

    (session created) --[any message]--> AWAITING_EMAIL
    AWAITING_EMAIL --[valid email]--> AWAITING_OTP
    AWAITING_OTP --[correct code]--> AWAITING_CASE_CHOICE
    AWAITING_OTP --[expired/locked]--> AWAITING_EMAIL
    AWAITING_CASE_CHOICE --["new"]--> ACTIVE (new case created)
    AWAITING_CASE_CHOICE --["existing", case found]--> AWAITING_EXISTING_CASE_CONFIRM
    AWAITING_CASE_CHOICE --["existing", none found]--> AWAITING_CASE_REFERENCE
    AWAITING_EXISTING_CASE_CONFIRM --["yes"]--> ACTIVE
    AWAITING_EXISTING_CASE_CONFIRM --["no"]--> AWAITING_CASE_REFERENCE
    AWAITING_CASE_REFERENCE --[known case id]--> ACTIVE
    ACTIVE --[any message]--> ACTIVE (logged against the case)

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

from app.messaging import gmail, twilio_client
from app.storage import identity_repository as repo
from app.storage.models import ChannelType, MessageDirection, SessionState, User, UserSession

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
    """

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

        if is_new:
            repo.set_initial_message(db, session, text)
            if channel_type == ChannelType.EMAIL:
                reply = self._start_email_session(db, session)
            else:
                reply = (
                    "Welcome! Before we get started, could you tell me the email address "
                    "you'd like to use to verify your identity?"
                )
        else:
            reply = self._advance(db, session, text.strip())

        self._reply(db, session, reply)
        logger.info("session=%s state=%s: outbound %r", session.id, session.state.value, reply)
        return {"session_id": session.id, "state": session.state.value, "reply": reply}

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
            repo.link_case(db, session, case.id)
            repo.set_state(db, session, SessionState.ACTIVE)
            logger.info(
                "session=%s user=%s: no existing cases, created case=%s, state -> ACTIVE",
                session.id, user.id, case.id,
            )
            return (
                f"Thanks — I couldn't find any existing cases for your email address, "
                f"so I've started a new one: {case.id}. How can I help with it?"
            )

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
            repo.link_case(db, session, case.id)
            repo.set_state(db, session, SessionState.ACTIVE)
            logger.info(
                "session=%s user=%s: created case=%s, state -> ACTIVE",
                session.id, session.user_id, case.id,
            )
            return f"Started new case {case.id}. How can I help with it?"

        if _matches_any(text, _EXISTING_CASE_WORDS):
            recent = repo.get_most_recent_case(db, session.user_id)
            if recent is None:
                repo.set_state(db, session, SessionState.AWAITING_CASE_REFERENCE)
                logger.info(
                    "session=%s user=%s: no existing cases found, state -> AWAITING_CASE_REFERENCE",
                    session.id, session.user_id,
                )
                return (
                    "I couldn't find any existing cases for you. Could you provide the "
                    "case reference id you'd like to continue?"
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
            repo.link_case(db, session, session.pending_case_id)
            case_id = session.pending_case_id
            repo.set_pending_case(db, session, None)
            repo.set_state(db, session, SessionState.ACTIVE)
            logger.info(
                "session=%s user=%s: confirmed case=%s, state -> ACTIVE",
                session.id, session.user_id, case_id,
            )
            return f"Great, continuing case {case_id}. How can I help?"

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
        repo.link_case(db, session, case.id)
        repo.set_state(db, session, SessionState.ACTIVE)
        logger.info(
            "session=%s user=%s: linked case=%s, state -> ACTIVE",
            session.id, session.user_id, case.id,
        )
        return f"Thanks, continuing case {case.id}. How can I help?"

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

        repo.link_case(db, session, case.id)
        repo.set_pending_case_choices(db, session, [])
        repo.set_state(db, session, SessionState.ACTIVE)
        logger.info(
            "session=%s user=%s: selected case=%s, state -> ACTIVE",
            session.id, session.user_id, case.id,
        )
        return f"Great, continuing case {case.id}. How can I help?"

    def _handle_active(self, db: DBSession, session: UserSession, text: str) -> str:
        repo.touch_case(db, session.case_id, summary=text)
        logger.info(
            "session=%s user=%s case=%s: message logged against active case",
            session.id, session.user_id, session.case_id,
        )
        return "Got it — I've logged your message on this case. We'll follow up shortly."

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
