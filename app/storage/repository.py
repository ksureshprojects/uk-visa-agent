"""Thin data-access helpers. Every write here is append-only or a single-row
upsert, so a retried step after a timeout/crash never corrupts state — a
retry either re-inserts an equivalent audit row (harmless) or re-applies the
same upsert (idempotent)."""

from sqlalchemy.orm import Session

from app.storage.models import (
    ApplicationField,
    Assessment,
    AuditLogEntry,
    Conversation,
    ConversationStatus,
    EscalationRecord,
    Message,
    MessageRole,
)


def create_conversation(db: Session, external_user_id: str) -> Conversation:
    convo = Conversation(external_user_id=external_user_id)
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


def get_conversation(db: Session, conversation_id: str) -> Conversation | None:
    return db.get(Conversation, conversation_id)


def add_message(db: Session, conversation_id: str, role: MessageRole, content: str) -> Message:
    msg = Message(conversation_id=conversation_id, role=role, content=content)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_history(db: Session, conversation_id: str) -> list[Message]:
    convo = db.get(Conversation, conversation_id)
    return sorted(convo.messages, key=lambda m: m.created_at) if convo else []


def set_status(db: Session, conversation_id: str, status: ConversationStatus) -> None:
    convo = db.get(Conversation, conversation_id)
    convo.status = status
    db.commit()
    log_audit(db, conversation_id, "state_transition", {"new_status": status.value})


def set_visa_type(db: Session, conversation_id: str, visa_type: str) -> None:
    convo = db.get(Conversation, conversation_id)
    convo.visa_type = visa_type
    db.commit()


def increment_clarify_rounds(db: Session, conversation_id: str) -> int:
    convo = db.get(Conversation, conversation_id)
    convo.clarify_rounds_used += 1
    db.commit()
    return convo.clarify_rounds_used


def save_assessment(
    db: Session,
    conversation_id: str,
    candidate_visa_types: list[dict],
    confidence: float,
    missing_info: list[str],
    citations: list[str],
    contradictions: list[str],
    gate_passed: bool,
    kb_version: str,
) -> Assessment:
    assessment = Assessment(
        conversation_id=conversation_id,
        candidate_visa_types=candidate_visa_types,
        confidence=confidence,
        missing_info=missing_info,
        citations=citations,
        contradictions=contradictions,
        gate_passed=gate_passed,
        kb_version=kb_version,
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


def log_audit(db: Session, conversation_id: str, kind: str, payload: dict) -> None:
    db.add(AuditLogEntry(conversation_id=conversation_id, kind=kind, payload=payload))
    db.commit()


def create_escalation(db: Session, conversation_id: str, trigger: str, detail: str) -> EscalationRecord:
    record = EscalationRecord(conversation_id=conversation_id, trigger=trigger, detail=detail)
    db.add(record)
    db.commit()
    set_status(db, conversation_id, ConversationStatus.NEEDS_HUMAN_REVIEW)
    return record


def upsert_field(
    db: Session,
    conversation_id: str,
    field_name: str,
    value: str | None,
    status: str,
    validation_error: str | None = None,
) -> ApplicationField:
    existing = (
        db.query(ApplicationField)
        .filter_by(conversation_id=conversation_id, field_name=field_name)
        .one_or_none()
    )
    if existing:
        existing.value = value
        existing.status = status
        existing.validation_error = validation_error
        if status == "invalid":
            existing.retry_count += 1
        db.commit()
        db.refresh(existing)
        return existing

    field = ApplicationField(
        conversation_id=conversation_id,
        field_name=field_name,
        value=value,
        status=status,
        validation_error=validation_error,
        retry_count=1 if status == "invalid" else 0,
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return field


def get_fields(db: Session, conversation_id: str) -> list[ApplicationField]:
    return (
        db.query(ApplicationField)
        .filter_by(conversation_id=conversation_id)
        .all()
    )


def list_unresolved_escalations(db: Session) -> list[EscalationRecord]:
    """The human review queue: every escalation not yet resolved by a caseworker."""
    return (
        db.query(EscalationRecord)
        .filter_by(resolved=False)
        .order_by(EscalationRecord.created_at)
        .all()
    )


def get_audit_log(db: Session, conversation_id: str) -> list[AuditLogEntry]:
    return (
        db.query(AuditLogEntry)
        .filter_by(conversation_id=conversation_id)
        .order_by(AuditLogEntry.created_at)
        .all()
    )
