import datetime
import enum
import uuid

from sqlalchemy import DateTime, Enum, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


class Base(DeclarativeBase):
    pass


class ConversationStatus(str, enum.Enum):
    ADVISORY = "advisory"  # Phase 1 in progress
    NEEDS_HUMAN_REVIEW = "needs_human_review"  # escalated, autonomous flow paused
    ASSEMBLY = "assembly"  # Phase 2 in progress
    READY_FOR_HUMAN_REVIEW = "ready_for_human_review"  # package drafted, awaiting sign-off
    COMPLETED = "completed"


class MessageRole(str, enum.Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"  # status/escalation notices shown to the user


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    external_user_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus), default=ConversationStatus.ADVISORY
    )
    visa_type: Mapped[str | None] = mapped_column(String, nullable=True)
    clarify_rounds_used: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")
    assessments: Mapped[list["Assessment"]] = relationship(back_populates="conversation")
    fields: Mapped[list["ApplicationField"]] = relationship(back_populates="conversation")
    audit_entries: Mapped[list["AuditLogEntry"]] = relationship(back_populates="conversation")
    escalations: Mapped[list["EscalationRecord"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Assessment(Base):
    """A snapshot of Phase 1 structured output at one point in the conversation."""

    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    candidate_visa_types: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    missing_info: Mapped[dict] = mapped_column(JSON)
    citations: Mapped[dict] = mapped_column(JSON)
    contradictions: Mapped[dict] = mapped_column(JSON)
    gate_passed: Mapped[bool] = mapped_column(default=False)
    kb_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="assessments")


class ApplicationField(Base):
    """One required field of the Phase 2 schema for the conversation's visa type."""

    __tablename__ = "application_fields"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    field_name: Mapped[str] = mapped_column(String)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|valid|invalid
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="fields")


class AuditLogEntry(Base):
    """Append-only record of every LLM call, retrieval, and state transition."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    kind: Mapped[str] = mapped_column(String)  # llm_call|retrieval|state_transition|validation
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="audit_entries")


class EscalationRecord(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    trigger: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="escalations")


# --- Identity verification & case management -----------------------------
#
# A separate model group from Conversation/Message above: those model the
# visa-advisory pipeline's own turn-by-turn state, while the models below
# model *who is messaging and which case they mean*, across whichever
# channel (WhatsApp or email) they used. A UserSession owns that
# channel-facing state machine (identity verification, then case
# selection); once a case is chosen, ChannelMessage is the append-only,
# per-message record of what was sent/received and over which channel.


class ChannelType(str, enum.Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class SessionState(str, enum.Enum):
    AWAITING_EMAIL = "awaiting_email"
    AWAITING_OTP = "awaiting_otp"
    AWAITING_CASE_CHOICE = "awaiting_case_choice"
    AWAITING_EXISTING_CASE_CONFIRM = "awaiting_existing_case_confirm"
    AWAITING_CASE_REFERENCE = "awaiting_case_reference"
    AWAITING_CASE_SELECTION = "awaiting_case_selection"
    ACTIVE = "active"


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class User(Base):
    """A person, identified solely by their (OTP-verified) email address —
    regardless of which channel(s) they message through."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    cases: Mapped[list["Case"]] = relationship(back_populates="user")
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")


class Case(Base):
    """One enquiry a user is pursuing, spanning any number of messages and
    sessions. Attributed to the user's email address via user_id."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="cases")
    messages: Mapped[list["ChannelMessage"]] = relationship(
        back_populates="case", foreign_keys="ChannelMessage.case_id"
    )


class UserSession(Base):
    """One interaction window for a channel identifier (a phone number or
    email address), from a user's first inbound message until 30 minutes of
    inactivity (SESSION_IDLE_TIMEOUT_MINUTES). Owns email+OTP identity
    verification and case selection before messages get attributed to a
    Case; `initial_message` caches the message that started the session so
    it can be acknowledged once verification completes."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    channel_type: Mapped[ChannelType] = mapped_column(Enum(ChannelType))
    channel_identifier: Mapped[str] = mapped_column(String, index=True)
    state: Mapped[SessionState] = mapped_column(Enum(SessionState), default=SessionState.AWAITING_EMAIL)

    initial_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    pending_email: Mapped[str | None] = mapped_column(String, nullable=True)
    otp_code_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    otp_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    otp_attempts: Mapped[int] = mapped_column(default=0)

    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    pending_case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    # Comma-separated case ids, in the order they were presented, for the
    # AWAITING_CASE_SELECTION state (email channel: pick one of up to 5).
    pending_case_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    last_activity_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now, index=True)

    user: Mapped[User | None] = relationship(back_populates="sessions", foreign_keys=[user_id])
    messages: Mapped[list["ChannelMessage"]] = relationship(
        back_populates="session", foreign_keys="ChannelMessage.session_id"
    )


class ChannelMessage(Base):
    """Append-only log of every inbound/outbound message across channels —
    identity-verification exchanges (case_id null) as well as case
    messages. channel_type/channel_identifier are recorded per-message
    rather than only per-session, since the channel a verification code is
    sent to (always email) can differ from the session's own channel."""

    __tablename__ = "channel_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("user_sessions.id"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection))
    channel_type: Mapped[ChannelType] = mapped_column(Enum(ChannelType))
    channel_identifier: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    session: Mapped[UserSession] = relationship(back_populates="messages", foreign_keys=[session_id])
    case: Mapped[Case | None] = relationship(back_populates="messages", foreign_keys=[case_id])
