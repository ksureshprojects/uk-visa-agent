"""Data-access helpers for identity verification and case management,
mirroring the style of app/storage/repository.py: small, single-purpose
functions, each write committed immediately so a retried step is either a
harmless re-insert (messages) or an idempotent upsert (session/case state).
"""

import datetime
import hashlib
import secrets

from sqlalchemy.orm import Session as DBSession

from app.config import OTP_EXPIRY_MINUTES, OTP_LENGTH, OTP_MAX_ATTEMPTS, SESSION_IDLE_TIMEOUT_MINUTES
from app.storage.models import (
    Case,
    ChannelMessage,
    ChannelType,
    MessageDirection,
    SessionState,
    User,
    UserSession,
)


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _generate_otp_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))


def _hash_otp(session_id: str, code: str) -> str:
    return hashlib.sha256(f"{session_id}:{code}".encode()).hexdigest()


# --- Sessions --------------------------------------------------------------


def get_or_create_active_session(
    db: DBSession, channel_type: ChannelType, channel_identifier: str
) -> tuple[UserSession, bool]:
    """Return the channel identifier's active session — one whose last
    activity is within SESSION_IDLE_TIMEOUT_MINUTES — or start a new one.
    The second return value is True when a new session was created, i.e.
    this inbound message is the session's "initial message"."""
    cutoff = _now() - datetime.timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
    existing = (
        db.query(UserSession)
        .filter(
            UserSession.channel_type == channel_type,
            UserSession.channel_identifier == channel_identifier,
            UserSession.last_activity_at >= cutoff,
        )
        .order_by(UserSession.last_activity_at.desc())
        .first()
    )
    if existing:
        return existing, False

    session = UserSession(channel_type=channel_type, channel_identifier=channel_identifier)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, True


def touch_activity(db: DBSession, session: UserSession) -> None:
    session.last_activity_at = _now()
    db.commit()


def set_state(db: DBSession, session: UserSession, state: SessionState) -> None:
    session.state = state
    db.commit()


def set_initial_message(db: DBSession, session: UserSession, text: str) -> None:
    session.initial_message = text
    db.commit()


def store_pending_email(db: DBSession, session: UserSession, email: str) -> None:
    session.pending_email = email.strip().lower()
    db.commit()


def clear_verification(db: DBSession, session: UserSession) -> None:
    session.pending_email = None
    session.otp_code_hash = None
    session.otp_expires_at = None
    session.otp_attempts = 0
    db.commit()


def issue_otp(db: DBSession, session: UserSession) -> str:
    code = _generate_otp_code()
    session.otp_code_hash = _hash_otp(session.id, code)
    session.otp_expires_at = _now() + datetime.timedelta(minutes=OTP_EXPIRY_MINUTES)
    session.otp_attempts = 0
    db.commit()
    return code


def verify_otp(db: DBSession, session: UserSession, submitted_code: str) -> str:
    """Check `submitted_code` against the session's pending OTP. Returns
    "verified", "expired" (no code issued, or past OTP_EXPIRY_MINUTES),
    "locked" (OTP_MAX_ATTEMPTS reached), or "mismatch"."""
    if session.otp_code_hash is None or session.otp_expires_at is None:
        return "expired"
    if _now() > session.otp_expires_at:
        return "expired"
    if session.otp_attempts >= OTP_MAX_ATTEMPTS:
        return "locked"

    session.otp_attempts += 1
    matches = secrets.compare_digest(
        session.otp_code_hash, _hash_otp(session.id, submitted_code.strip())
    )
    if matches:
        session.otp_code_hash = None
        session.otp_expires_at = None
        db.commit()
        return "verified"

    result = "locked" if session.otp_attempts >= OTP_MAX_ATTEMPTS else "mismatch"
    db.commit()
    return result


def link_user(db: DBSession, session: UserSession, user: User) -> None:
    session.user_id = user.id
    db.commit()


def set_pending_case(db: DBSession, session: UserSession, case_id: str | None) -> None:
    session.pending_case_id = case_id
    db.commit()


def link_case(db: DBSession, session: UserSession, case_id: str) -> None:
    session.case_id = case_id
    db.commit()


# --- Users -------------------------------------------------------------


def find_or_create_user(db: DBSession, email: str) -> User:
    normalized = email.strip().lower()
    user = db.query(User).filter_by(email=normalized).one_or_none()
    if user:
        return user
    user = User(email=normalized)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --- Cases -------------------------------------------------------------


def create_case(db: DBSession, user_id: str, summary: str | None = None) -> Case:
    case = Case(user_id=user_id, summary=summary)
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def get_case(db: DBSession, case_id: str) -> Case | None:
    return db.get(Case, case_id)


def get_most_recent_case(db: DBSession, user_id: str) -> Case | None:
    return (
        db.query(Case)
        .filter_by(user_id=user_id)
        .order_by(Case.updated_at.desc())
        .first()
    )


def touch_case(db: DBSession, case_id: str, summary: str | None = None) -> None:
    case = db.get(Case, case_id)
    if case is None:
        return
    if summary is not None:
        case.summary = summary
    case.updated_at = _now()
    db.commit()


# --- Messages ------------------------------------------------------------


def log_message(
    db: DBSession,
    session_id: str,
    case_id: str | None,
    direction: MessageDirection,
    channel_type: ChannelType,
    channel_identifier: str,
    content: str,
) -> ChannelMessage:
    message = ChannelMessage(
        session_id=session_id,
        case_id=case_id,
        direction=direction,
        channel_type=channel_type,
        channel_identifier=channel_identifier,
        content=content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
