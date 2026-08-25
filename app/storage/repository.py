"""Thin data-access helpers. Every write here is append-only or a single-row
upsert, so a retried step after a timeout/crash never corrupts state — a
retry either re-inserts an equivalent audit row (harmless) or re-applies the
same upsert (idempotent).

Naming convention: functions take `case_id` when they touch case-level
workflow state (status, visa_type, assessments, fields, escalations, audit
log — the stuff a case keeps regardless of which channel it's discussed on)
and `conversation_id` when they touch one channel thread's messages.
"""

import datetime

from sqlalchemy.orm import Session

from app.config import MAX_OTP_ATTEMPTS, OTP_TTL_MINUTES
from app.workflow.linking import generate_case_reference, hash_code

from app.storage.models import (
    ApplicationField,
    Assessment,
    AuditLogEntry,
    Case,
    CaseIdentityLink,
    CaseLinkVerification,
    CaseStatus,
    ChannelType,
    Conversation,
    EscalationRecord,
    Identity,
    IdentityRole,
    Message,
    MessageRole,
)


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


# ---------------------------------------------------------------------------
# Identities
# ---------------------------------------------------------------------------


def find_or_create_identity(db: Session, channel: ChannelType, address: str) -> Identity:
    existing = db.query(Identity).filter_by(channel=channel, address=address).one_or_none()
    if existing:
        return existing
    identity = Identity(channel=channel, address=address)
    db.add(identity)
    db.commit()
    db.refresh(identity)
    return identity


def get_linked_identities(db: Session, case_id: str) -> list[Identity]:
    """All identities currently allowed to act on this case (originating + linked)."""
    return (
        db.query(Identity)
        .join(CaseIdentityLink, CaseIdentityLink.identity_id == Identity.id)
        .filter(CaseIdentityLink.case_id == case_id)
        .all()
    )


def is_identity_linked_to_case(db: Session, identity_id: str, case_id: str) -> bool:
    return (
        db.query(CaseIdentityLink).filter_by(identity_id=identity_id, case_id=case_id).one_or_none()
        is not None
    )


def link_identity_to_case(
    db: Session, case_id: str, identity_id: str, role: IdentityRole
) -> CaseIdentityLink:
    link = CaseIdentityLink(case_id=case_id, identity_id=identity_id, role=role)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


# ---------------------------------------------------------------------------
# Cases + threads
# ---------------------------------------------------------------------------


def create_case(db: Session, identity: Identity) -> tuple[Case, Conversation]:
    """Start a brand new case with `identity` as its originating identity,
    plus that identity's first conversation thread."""
    reference = generate_case_reference()
    while db.query(Case).filter_by(reference=reference).one_or_none() is not None:
        reference = generate_case_reference()

    case = Case(reference=reference)
    db.add(case)
    db.commit()
    db.refresh(case)

    link_identity_to_case(db, case.id, identity.id, role=IdentityRole.ORIGINATING)
    thread = get_or_create_thread(db, case.id, identity.id)
    return case, thread


def get_case(db: Session, case_id: str) -> Case | None:
    return db.get(Case, case_id)


def get_case_by_reference(db: Session, reference: str) -> Case | None:
    return db.query(Case).filter_by(reference=reference.upper()).one_or_none()


def get_open_case_for_identity(db: Session, identity_id: str) -> Case | None:
    """The identity's one open (non-completed) case, if any. v1 scope
    decision (MULTICHANNEL.md §8): one open case per identity — an
    identity already linked to an open case is always routed back into
    it rather than disambiguating between several."""
    return (
        db.query(Case)
        .join(CaseIdentityLink, CaseIdentityLink.case_id == Case.id)
        .filter(CaseIdentityLink.identity_id == identity_id, Case.status != CaseStatus.COMPLETED)
        .order_by(Case.created_at.desc())
        .first()
    )


def get_or_create_thread(db: Session, case_id: str, identity_id: str) -> Conversation:
    existing = db.query(Conversation).filter_by(case_id=case_id, identity_id=identity_id).one_or_none()
    if existing:
        return existing
    thread = Conversation(case_id=case_id, identity_id=identity_id)
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


def get_originating_thread(db: Session, case_id: str) -> Conversation | None:
    link = (
        db.query(CaseIdentityLink)
        .filter_by(case_id=case_id, role=IdentityRole.ORIGINATING)
        .one_or_none()
    )
    if link is None:
        return None
    return get_or_create_thread(db, case_id, link.identity_id)


def get_conversation(db: Session, conversation_id: str) -> Conversation | None:
    return db.get(Conversation, conversation_id)


def set_status(db: Session, case_id: str, status: CaseStatus) -> None:
    case = db.get(Case, case_id)
    case.status = status
    db.commit()
    log_audit(db, case_id, "state_transition", {"new_status": status.value})


def set_visa_type(db: Session, case_id: str, visa_type: str) -> None:
    case = db.get(Case, case_id)
    case.visa_type = visa_type
    db.commit()


def increment_clarify_rounds(db: Session, case_id: str) -> int:
    case = db.get(Case, case_id)
    case.clarify_rounds_used += 1
    db.commit()
    return case.clarify_rounds_used


# ---------------------------------------------------------------------------
# Messages (thread-scoped)
# ---------------------------------------------------------------------------


def add_message(db: Session, conversation_id: str, role: MessageRole, content: str) -> Message:
    msg = Message(conversation_id=conversation_id, role=role, content=content)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_thread_history(db: Session, conversation_id: str) -> list[Message]:
    convo = db.get(Conversation, conversation_id)
    return sorted(convo.messages, key=lambda m: m.created_at) if convo else []


def get_case_history(db: Session, case_id: str) -> list[Message]:
    """Every message across every channel thread of this case, in order —
    this is what lets the agent carry context across channels (§8)."""
    return (
        db.query(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.case_id == case_id)
        .order_by(Message.created_at)
        .all()
    )


# ---------------------------------------------------------------------------
# Case-level workflow state
# ---------------------------------------------------------------------------


def save_assessment(
    db: Session,
    case_id: str,
    candidate_visa_types: list[dict],
    confidence: float,
    missing_info: list[str],
    citations: list[str],
    contradictions: list[str],
    gate_passed: bool,
    kb_version: str,
) -> Assessment:
    assessment = Assessment(
        case_id=case_id,
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


def log_audit(db: Session, case_id: str, kind: str, payload: dict) -> None:
    db.add(AuditLogEntry(case_id=case_id, kind=kind, payload=payload))
    db.commit()


def create_escalation(db: Session, case_id: str, trigger: str, detail: str) -> EscalationRecord:
    record = EscalationRecord(case_id=case_id, trigger=trigger, detail=detail)
    db.add(record)
    db.commit()
    set_status(db, case_id, CaseStatus.NEEDS_HUMAN_REVIEW)
    return record


def upsert_field(
    db: Session,
    case_id: str,
    field_name: str,
    value: str | None,
    status: str,
    validation_error: str | None = None,
) -> ApplicationField:
    existing = db.query(ApplicationField).filter_by(case_id=case_id, field_name=field_name).one_or_none()
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
        case_id=case_id,
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


def get_fields(db: Session, case_id: str) -> list[ApplicationField]:
    return db.query(ApplicationField).filter_by(case_id=case_id).all()


def list_unresolved_escalations(db: Session) -> list[EscalationRecord]:
    """The human review queue: every escalation not yet resolved by a caseworker."""
    return db.query(EscalationRecord).filter_by(resolved=False).order_by(EscalationRecord.created_at).all()


def get_audit_log(db: Session, case_id: str) -> list[AuditLogEntry]:
    return db.query(AuditLogEntry).filter_by(case_id=case_id).order_by(AuditLogEntry.created_at).all()


# ---------------------------------------------------------------------------
# Cross-channel link verification (OTP flow, MULTICHANNEL.md §6-7)
# ---------------------------------------------------------------------------


def create_link_verification(
    db: Session, case_id: str, requesting_identity_id: str, target_identity_id: str, code: str
) -> CaseLinkVerification:
    verification = CaseLinkVerification(
        case_id=case_id,
        requesting_identity_id=requesting_identity_id,
        target_identity_id=target_identity_id,
        code_hash=hash_code(code),
        expires_at=_now() + datetime.timedelta(minutes=OTP_TTL_MINUTES),
    )
    db.add(verification)
    db.commit()
    db.refresh(verification)
    return verification


def get_link_verification(db: Session, verification_id: str) -> CaseLinkVerification | None:
    return db.get(CaseLinkVerification, verification_id)


def get_active_verification_for_identity(db: Session, identity_id: str) -> CaseLinkVerification | None:
    """The most recent still-usable (unconsumed, unexpired, attempts left)
    verification this identity requested, if any — used to recognize an
    inbound message as "this is probably the OTP reply" (§6)."""
    return (
        db.query(CaseLinkVerification)
        .filter(
            CaseLinkVerification.requesting_identity_id == identity_id,
            CaseLinkVerification.consumed_at.is_(None),
            CaseLinkVerification.attempt_count < MAX_OTP_ATTEMPTS,
            CaseLinkVerification.expires_at > _now(),
        )
        .order_by(CaseLinkVerification.created_at.desc())
        .first()
    )


def count_recent_link_requests(db: Session, case_id: str, since: datetime.datetime) -> int:
    return (
        db.query(CaseLinkVerification)
        .filter(CaseLinkVerification.case_id == case_id, CaseLinkVerification.created_at >= since)
        .count()
    )


def verify_link_code(db: Session, verification_id: str, code: str) -> bool:
    """Checks `code` against the stored verification, consuming it on a
    match. Returns False (without consuming) for: unknown/already-consumed
    verification, attempts exhausted, expiry, or a wrong code — callers
    that need to distinguish "wrong code, try again" from "this
    verification is dead" should re-fetch via get_link_verification after
    a False result and inspect attempt_count/expires_at/consumed_at."""
    verification = db.get(CaseLinkVerification, verification_id)
    if (
        verification is None
        or verification.consumed_at is not None
        or verification.attempt_count >= MAX_OTP_ATTEMPTS
    ):
        return False

    verification.attempt_count += 1
    matched = _now() <= verification.expires_at and hash_code(code) == verification.code_hash
    if matched:
        verification.consumed_at = _now()
    db.commit()
    return matched
