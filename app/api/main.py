from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import BASE_DIR
from app.kb.retrieval import KnowledgeStore
from app.llm import get_llm_provider
from app.storage import repository
from app.storage.db import get_session, init_db
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


class StartConversationRequest(BaseModel):
    external_user_id: str


class StartConversationResponse(BaseModel):
    conversation_id: str


class MessageRequest(BaseModel):
    text: str


class MessageResponse(BaseModel):
    reply_to_user: str
    status: str
    escalated: bool = False
    package: dict | None = None


@app.post("/conversations", response_model=StartConversationResponse)
def start_conversation(
    req: StartConversationRequest, orchestrator: Orchestrator = Depends(get_orchestrator)
) -> StartConversationResponse:
    db = get_session()
    try:
        convo = orchestrator.start_conversation(db, req.external_user_id)
        return StartConversationResponse(conversation_id=convo.id)
    finally:
        db.close()


@app.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
def post_message(
    conversation_id: str, req: MessageRequest, orchestrator: Orchestrator = Depends(get_orchestrator)
) -> MessageResponse:
    db = get_session()
    try:
        try:
            result = orchestrator.handle_message(db, conversation_id, req.text)
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


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    db = get_session()
    try:
        convo = repository.get_conversation(db, conversation_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="Not found")
        return {
            "id": convo.id,
            "status": convo.status.value,
            "visa_type": convo.visa_type,
            "messages": [
                {"role": m.role.value, "content": m.content, "created_at": m.created_at.isoformat()}
                for m in sorted(convo.messages, key=lambda m: m.created_at)
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
                    "conversation_id": r.conversation_id,
                    "trigger": r.trigger,
                    "detail": r.detail,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ]
        }
    finally:
        db.close()


@app.get("/admin/conversations/{conversation_id}/audit")
def get_audit_log(conversation_id: str) -> dict:
    """Full audit trail for one conversation: every LLM call, retrieval, and
    state transition, in order. This is what makes service audits and model
    improvement possible (see ARCHITECTURE.md section 4)."""
    db = get_session()
    try:
        entries = repository.get_audit_log(db, conversation_id)
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
