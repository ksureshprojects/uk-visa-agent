"""Twilio webhook stubs: separate inbound endpoints for WhatsApp and email,
each handing the raw payload to IdentitySessionManager after pulling out
the sender and message text. Field names follow Twilio's own webhook
conventions:

- WhatsApp: Twilio's Messaging webhook posts `From` (e.g.
  "whatsapp:+14155551234") and `Body` as form fields.
  https://www.twilio.com/docs/messaging/guides/webhook-request

- Email: Twilio SendGrid's Inbound Parse webhook posts `from` and `text`
  (among other fields) as multipart form fields.
  https://www.twilio.com/docs/sendgrid/for-developers/parsing-email/setting-up-the-inbound-parse-webhook
"""

from fastapi import APIRouter, Form

from app.identity.session_manager import IdentitySessionManager
from app.storage.db import get_session
from app.storage.models import ChannelType

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])

_manager = IdentitySessionManager()


def _strip_whatsapp_prefix(value: str) -> str:
    return value[len("whatsapp:") :] if value.startswith("whatsapp:") else value


def _extract_email_address(from_header: str) -> str:
    """Inbound Parse's `from` field is a full header value, e.g.
    '"Jane Doe" <jane@example.com>' — pull out just the address."""
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0].strip()
    return from_header.strip()


@router.post("/whatsapp")
def inbound_whatsapp(From: str = Form(...), Body: str = Form(...)) -> dict:
    db = get_session()
    try:
        sender = _strip_whatsapp_prefix(From)
        return _manager.handle_inbound_message(db, ChannelType.WHATSAPP, sender, Body)
    finally:
        db.close()


@router.post("/email")
def inbound_email(From: str = Form(...), text: str = Form("")) -> dict:
    db = get_session()
    try:
        sender = _extract_email_address(From)
        return _manager.handle_inbound_message(db, ChannelType.EMAIL, sender, text)
    finally:
        db.close()
