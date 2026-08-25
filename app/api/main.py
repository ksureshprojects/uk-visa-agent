from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import BASE_DIR
from app.kb.retrieval import KnowledgeStore
from app.llm import get_llm_provider
from app.storage import repository
from app.storage.db import get_session, init_db
from app.storage.models import ChannelType
from app.workflow.orchestrator import Orchestrator

app = FastAPI(title="UK Visa Agent Demo")

_state: dict = {"orchestrator": None}


@app.on_event("startup")
def startup() -> None:
    init_db()
    kb = KnowledgeStore()
    llm = get_llm_provider()
    _state["orchestrator"] = Orchestrator(llm, kb)


def get_orchestrator() -> Orchestrator:
    orchestrator = _state["orchestrator"]
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Service is starting up")
    return orchestrator


class StartCaseRequest(BaseModel):
    external_user_id: str


class StartCaseResponse(BaseModel):
    case_id: str
    case_reference: str


class MessageRequest(BaseModel):
    text: str


class MessageResponse(BaseModel):
    reply_to_user: str
    status: str
    escalated: bool = False
    package: dict | None = None


class InboundChannelRequest(BaseModel):
    """Stand-in for a real Twilio webhook body. A live WhatsAppTransport /
    EmailTransport (MULTICHANNEL.md §4, not yet implemented) would parse
    Twilio's actual payload down to this same (address, text) shape and
    call orchestrator.route_inbound — this endpoint exists so the
    cross-channel routing/linking logic is exercisable end-to-end without
    a live Twilio account."""

    address: str
    text: str


class InboundChannelResponse(BaseModel):
    reply_to_user: str
    status: str
    case_reference: str | None = None


@app.post("/cases", response_model=StartCaseResponse)
def start_case(
    req: StartCaseRequest, orchestrator: Orchestrator = Depends(get_orchestrator)
) -> StartCaseResponse:
    db = get_session()
    try:
        case, _thread = orchestrator.start_case(db, ChannelType.WEB, req.external_user_id)
        return StartCaseResponse(case_id=case.id, case_reference=case.reference)
    finally:
        db.close()


@app.post("/cases/{case_id}/messages", response_model=MessageResponse)
def post_message(
    case_id: str, req: MessageRequest, orchestrator: Orchestrator = Depends(get_orchestrator)
) -> MessageResponse:
    db = get_session()
    try:
        thread = repository.get_originating_thread(db, case_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Unknown case")
        try:
            result = orchestrator.handle_message(db, case_id, thread.id, req.text)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return MessageResponse(
            reply_to_user=result["reply_to_user"],
            status=result["status"],
            escalated=result.get("escalated", False),
            package=result.get("package"),
        )
    finally:
        db.close()


@app.post("/channels/{channel}/inbound", response_model=InboundChannelResponse)
def channel_inbound(
    channel: ChannelType, req: InboundChannelRequest, orchestrator: Orchestrator = Depends(get_orchestrator)
) -> InboundChannelResponse:
    if channel == ChannelType.WEB:
        raise HTTPException(status_code=400, detail="Use POST /cases for the web channel")
    db = get_session()
    try:
        result = orchestrator.route_inbound(db, channel, req.address, req.text)
        return InboundChannelResponse(
            reply_to_user=result["reply_to_user"],
            status=result["status"],
            case_reference=result.get("case_reference"),
        )
    finally:
        db.close()


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> dict:
    db = get_session()
    try:
        case = repository.get_case(db, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Not found")
        history = repository.get_case_history(db, case_id)
        identities = repository.get_linked_identities(db, case_id)
        links = {link.identity_id: link.role.value for link in case.identity_links}
        return {
            "id": case.id,
            "case_reference": case.reference,
            "status": case.status.value,
            "visa_type": case.visa_type,
            "identities": [
                {"channel": i.channel.value, "address": i.address, "role": links.get(i.id)} for i in identities
            ],
            "messages": [
                {
                    "role": m.role.value,
                    "content": m.content,
                    "channel": m.conversation.identity.channel.value,
                    "created_at": m.created_at.isoformat(),
                }
                for m in history
            ],
        }
    finally:
        db.close()


@app.get("/admin/escalations")
def list_escalations() -> dict:
    """The human review queue: what a caseworker would triage next."""
    db = get_session()
    try:
        records = repository.list_unresolved_escalations(db)
        return {
            "escalations": [
                {
                    "id": r.id,
                    "case_id": r.case_id,
                    "trigger": r.trigger,
                    "detail": r.detail,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ]
        }
    finally:
        db.close()


@app.get("/admin/cases/{case_id}/audit")
def get_audit_log(case_id: str) -> dict:
    """Full audit trail for one case: every LLM call, retrieval, and
    state transition, in order. This is what makes service audits and model
    improvement possible (see ARCHITECTURE.md section 4)."""
    db = get_session()
    try:
        entries = repository.get_audit_log(db, case_id)
        return {
            "entries": [
                {"kind": e.kind, "payload": e.payload, "created_at": e.created_at.isoformat()}
                for e in entries
            ]
        }
    finally:
        db.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
