import datetime
import enum
import uuid

from sqlalchemy import DateTime, Enum, Float, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


class Base(DeclarativeBase):
    pass


class CaseStatus(str, enum.Enum):
    ADVISORY = "advisory"  # Phase 1 in progress
    NEEDS_HUMAN_REVIEW = "needs_human_review"  # escalated, autonomous flow paused
    ASSEMBLY = "assembly"  # Phase 2 in progress
    READY_FOR_HUMAN_REVIEW = "ready_for_human_review"  # package drafted, awaiting sign-off
    COMPLETED = "completed"


class MessageRole(str, enum.Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"  # status/escalation notices shown to the user


class ChannelType(str, enum.Enum):
    WEB = "web"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class IdentityRole(str, enum.Enum):
    ORIGINATING = "originating"  # the identity that opened the case
    LINKED = "linked"  # a second channel verified onto the case via OTP


class Case(Base):
    """The aggregate root for one visa case: everything the checkpoint gate,
    assembly engine, and escalation rules operate on. Reachable from one or
    more channel Identities (see CaseIdentityLink) but owns none of them —
    a case outlives any single channel thread."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus), default=CaseStatus.ADVISORY)
    visa_type: Mapped[str | None] = mapped_column(String, nullable=True)
    clarify_rounds_used: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="case")
    identity_links: Mapped[list["CaseIdentityLink"]] = relationship(back_populates="case")
    assessments: Mapped[list["Assessment"]] = relationship(back_populates="case")
    fields: Mapped[list["ApplicationField"]] = relationship(back_populates="case")
    audit_entries: Mapped[list["AuditLogEntry"]] = relationship(back_populates="case")
    escalations: Mapped[list["EscalationRecord"]] = relationship(back_populates="case")
    link_verifications: Mapped[list["CaseLinkVerification"]] = relationship(back_populates="case")


class Identity(Base):
    """A single channel-scoped address we've seen traffic from — not a
    verified person record, just a normalized (channel, address) handle.
    Becomes "the originating identity" of a case the moment it starts one,
    and other identities can be added to the same case only via the OTP
    link flow (app/workflow/linking.py)."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("channel", "address", name="uq_identity_channel_address"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    channel: Mapped[ChannelType] = mapped_column(Enum(ChannelType))
    address: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    case_links: Mapped[list["CaseIdentityLink"]] = relationship(back_populates="identity")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="identity")


class CaseIdentityLink(Base):
    """Which identities may act on which case. The `originating` link is
    created once, at case creation, and never changes; `linked` rows are
    added by a successful OTP verification (CaseLinkVerification)."""

    __tablename__ = "case_identity_links"
    __table_args__ = (UniqueConstraint("case_id", "identity_id", name="uq_case_identity_link"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    role: Mapped[IdentityRole] = mapped_column(Enum(IdentityRole))
    linked_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    case: Mapped[Case] = relationship(back_populates="identity_links")
    identity: Mapped[Identity] = relationship(back_populates="case_links")


class Conversation(Base):
    """A per-channel message thread belonging to a Case. A case with two
    linked channels has two Conversation rows, one per (case, identity) —
    this is what keeps per-channel message history/formatting separate
    while the workflow state (Case) is shared."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("case_id", "identity_id", name="uq_conversation_case_identity"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    case: Mapped[Case] = relationship(back_populates="conversations")
    identity: Mapped[Identity] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Assessment(Base):
    """A snapshot of Phase 1 structured output at one point in the case."""

    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    candidate_visa_types: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    missing_info: Mapped[dict] = mapped_column(JSON)
    citations: Mapped[dict] = mapped_column(JSON)
    contradictions: Mapped[dict] = mapped_column(JSON)
    gate_passed: Mapped[bool] = mapped_column(default=False)
    kb_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    case: Mapped[Case] = relationship(back_populates="assessments")


class ApplicationField(Base):
    """One required field of the Phase 2 schema for the case's visa type."""

    __tablename__ = "application_fields"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    field_name: Mapped[str] = mapped_column(String)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|valid|invalid
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    case: Mapped[Case] = relationship(back_populates="fields")


class AuditLogEntry(Base):
    """Append-only record of every LLM call, retrieval, and state transition."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String)  # llm_call|retrieval|state_transition|validation
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    case: Mapped[Case] = relationship(back_populates="audit_entries")


class EscalationRecord(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    trigger: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    case: Mapped[Case] = relationship(back_populates="escalations")


class CaseLinkVerification(Base):
    """One OTP challenge issued to link a non-originating identity onto an
    existing case. The code itself is never stored, only its hash; a row
    is single-use (`consumed_at`) and capped on both attempts and expiry —
    see app/workflow/linking.py for the constants and app/workflow
    orchestrator for how this is issued/checked."""

    __tablename__ = "case_link_verifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    requesting_identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"), index=True)
    target_identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    code_hash: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    attempt_count: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    case: Mapped[Case] = relationship(back_populates="link_verifications")
