"""State machine driving identity verification and case selection for
inbound channel messages (WhatsApp or email).

This is intentionally decoupled from app/workflow/orchestrator.py's visa
advisory pipeline: this module owns *who is talking* (email-OTP verified
identity) and *which case they mean*, before a message would ever reach
the advisory agent. Wiring a verified, case-attributed message into that
pipeline is a follow-up, not part of this slice.

One state machine step per inbound message, driven by UserSession.state:

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
"""

import re

from sqlalchemy.orm import Session as DBSession

from app.messaging import twilio_client
from app.storage import identity_repository as repo
from app.storage.models import ChannelType, MessageDirection, SessionState, UserSession

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
        repo.touch_activity(db, session)
        repo.log_message(
            db, session.id, session.case_id, MessageDirection.INBOUND,
            channel_type, channel_identifier, text,
        )

        if is_new:
            repo.set_initial_message(db, session, text)
            reply = (
                "Welcome! Before we get started, could you tell me the email address "
                "you'd like to use to verify your identity?"
            )
        else:
            reply = self._advance(db, session, text.strip())

        self._reply(db, session, reply)
        return {"session_id": session.id, "state": session.state.value, "reply": reply}

    def _advance(self, db: DBSession, session: UserSession, text: str) -> str:
        handler = getattr(self, f"_handle_{session.state.value}", None)
        if handler is None:
            return "Sorry, something went wrong on our end. Please try again shortly."
        return handler(db, session, text)

    # --- state handlers ----------------------------------------------------

    def _handle_awaiting_email(self, db: DBSession, session: UserSession, text: str) -> str:
        email = text.strip().lower()
        if not _EMAIL_RE.match(email):
            return "That doesn't look like a valid email address. Could you send it again?"

        repo.store_pending_email(db, session, email)
        code = repo.issue_otp(db, session)
        otp_body = f"Your verification code is {code}. It expires in a few minutes."
        twilio_client.send_email(to=email, subject="Your verification code", body=otp_body)
        repo.log_message(
            db, session.id, session.case_id, MessageDirection.OUTBOUND,
            ChannelType.EMAIL, email, otp_body,
        )

        repo.set_state(db, session, SessionState.AWAITING_OTP)
        return f"We've sent a verification code to {email}. Please enter it here to continue."

    def _handle_awaiting_otp(self, db: DBSession, session: UserSession, text: str) -> str:
        result = repo.verify_otp(db, session, text)

        if result == "verified":
            user = repo.find_or_create_user(db, session.pending_email)
            repo.link_user(db, session, user)
            repo.set_state(db, session, SessionState.AWAITING_CASE_CHOICE)
            return (
                f'Thanks, you\'re verified. Picking up your earlier message: "{session.initial_message}". '
                "Would you like to start a new case, or continue an existing case?"
            )

        if result in ("expired", "locked"):
            repo.clear_verification(db, session)
            repo.set_state(db, session, SessionState.AWAITING_EMAIL)
            reason = "expired" if result == "expired" else "had too many incorrect attempts"
            return f"That verification code {reason}. Let's start again — what's your email address?"

        return "That code didn't match. Please double-check and enter it again."

    def _handle_awaiting_case_choice(self, db: DBSession, session: UserSession, text: str) -> str:
        if _matches_any(text, _NEW_CASE_WORDS):
            case = repo.create_case(db, session.user_id, summary=session.initial_message)
            repo.link_case(db, session, case.id)
            repo.set_state(db, session, SessionState.ACTIVE)
            return f"Started new case {case.id}. How can I help with it?"

        if _matches_any(text, _EXISTING_CASE_WORDS):
            recent = repo.get_most_recent_case(db, session.user_id)
            if recent is None:
                repo.set_state(db, session, SessionState.AWAITING_CASE_REFERENCE)
                return (
                    "I couldn't find any existing cases for you. Could you provide the "
                    "case reference id you'd like to continue?"
                )
            repo.set_pending_case(db, session, recent.id)
            repo.set_state(db, session, SessionState.AWAITING_EXISTING_CASE_CONFIRM)
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
            return f"Great, continuing case {case_id}. How can I help?"

        if _matches_any(text, _NO_WORDS):
            repo.set_pending_case(db, session, None)
            repo.set_state(db, session, SessionState.AWAITING_CASE_REFERENCE)
            return "No problem — could you provide the case reference id you'd like to continue?"

        return "Sorry, could you confirm with yes or no — is that the right case?"

    def _handle_awaiting_case_reference(self, db: DBSession, session: UserSession, text: str) -> str:
        case_id = text.strip()
        case = repo.get_case(db, case_id)
        if case is None or case.user_id != session.user_id:
            return (
                "I couldn't find a case with that reference id for your account. "
                "Could you check and send it again?"
            )
        repo.link_case(db, session, case.id)
        repo.set_state(db, session, SessionState.ACTIVE)
        return f"Thanks, continuing case {case.id}. How can I help?"

    def _handle_active(self, db: DBSession, session: UserSession, text: str) -> str:
        repo.touch_case(db, session.case_id, summary=text)
        return "Got it — I've logged your message on this case. We'll follow up shortly."

    # --- outbound dispatch ---------------------------------------------------

    def _reply(self, db: DBSession, session: UserSession, text: str) -> None:
        if session.channel_type == ChannelType.WHATSAPP:
            twilio_client.send_whatsapp(to=session.channel_identifier, body=text)
        else:
            twilio_client.send_email(to=session.channel_identifier, subject="UK Visa Agent", body=text)

        repo.log_message(
            db, session.id, session.case_id, MessageDirection.OUTBOUND,
            session.channel_type, session.channel_identifier, text,
        )
