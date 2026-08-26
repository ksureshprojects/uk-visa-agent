"""Outbound-message functions for the Twilio APIs this project sends
through: the Messaging API for WhatsApp, and SendGrid's Mail Send API for
email (SendGrid is Twilio's email product; there is no separate "Twilio
email" API).

send_whatsapp is wired to the real Messaging API. send_email is still a
stub pending SendGrid credentials — swap in the commented `httpx.post(...)`
call once TWILIO_SENDGRID_* config (app/config.py) is set for a real
account.

Callers (e.g. app/identity/session_manager.py) are written against the
real return shape already, so wiring these up later doesn't change any
call site.
"""

import logging
from typing import Any

import httpx

from app.config import (
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_SENDGRID_FROM_EMAIL,
    TWILIO_WHATSAPP_FROM,
)

logger = logging.getLogger(__name__)

TWILIO_MESSAGES_URL = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
SENDGRID_MAIL_SEND_URL = "https://api.sendgrid.com/v3/mail/send"


def send_whatsapp(to: str, body: str) -> dict[str, Any]:
    """Send a WhatsApp message via the Twilio Messaging API.

    `to` is a phone number in E.164 format (e.g. "+14155551234"), without
    the "whatsapp:" prefix — this function adds it, matching what Twilio's
    Messages resource expects for both `From` and `To`.
    """
    payload = {
        "From": f"whatsapp:{TWILIO_WHATSAPP_FROM}",
        "To": f"whatsapp:{to}",
        "Body": body,
    }
    logger.debug("Sending WhatsApp message to %s (%d chars)", to, len(body))
    response = httpx.post(
        TWILIO_MESSAGES_URL.format(account_sid=TWILIO_ACCOUNT_SID),
        data=payload,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.error(
            "WhatsApp send to %s failed: HTTP %s - %s", to, response.status_code, response.text,
        )
        raise
    result = response.json()
    logger.info(
        "WhatsApp message sent to %s (sid=%s, status=%s)",
        to, result.get("sid"), result.get("status"),
    )
    return result


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send an email via Twilio SendGrid's Mail Send API.

    STUB: not yet wired to a live SendGrid account. Once
    TWILIO_SENDGRID_API_KEY / TWILIO_SENDGRID_FROM_EMAIL are configured,
    replace the body below with:

        response = httpx.post(
            SENDGRID_MAIL_SEND_URL,
            json=payload,
            headers={"Authorization": f"Bearer {TWILIO_SENDGRID_API_KEY}"},
        )
        response.raise_for_status()
        return {"status": "sent"}
    """
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": TWILIO_SENDGRID_FROM_EMAIL},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    logger.info("Email send to %s stubbed (subject=%r)", to, subject)
    return {"status": "stubbed", "channel": "email", "request": payload}
