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
