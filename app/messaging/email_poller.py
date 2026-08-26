"""Polls the Gmail inbox for new messages and feeds them through
IdentitySessionManager — the email-channel equivalent of the WhatsApp
webhook in app/api/webhooks.py. Gmail has no inbound webhook, so this
polls instead. app/api/main.py runs run_forever() in a background thread
for the life of the app (IMAP/SMTP/DB calls here are all synchronous, so
this must not run directly on the asyncio event loop).

Can also run as a standalone process:
    .venv/bin/python -m app.messaging.email_poller
"""

import logging
import re
import threading

from app.config import EMAIL_POLL_INTERVAL_SECONDS, GMAIL_APP_PASSWORD, GMAIL_USER
from app.identity.session_manager import IdentitySessionManager
from app.messaging import gmail
from app.storage.db import get_session, init_db
from app.storage.models import ChannelType

logger = logging.getLogger(__name__)

_manager = IdentitySessionManager()

# The inbox this polls is a real mailbox, not a dedicated agent address, so
# most unread mail (newsletters, notifications, spam) is not a case enquiry
# at all. Only treat a message as one if both words appear somewhere in the
# subject or body — a cheap guard against auto-replying to unrelated mail.
_UK_RE = re.compile(r"\buk\b", re.IGNORECASE)
_VISA_RE = re.compile(r"\bvisa\b", re.IGNORECASE)


def _looks_like_visa_enquiry(message: dict) -> bool:
    haystack = f"{message['subject']} {message['body']}"
    return bool(_UK_RE.search(haystack) and _VISA_RE.search(haystack))


def poll_once() -> int:
    """Fetch all currently-unread messages and process the ones that look
    like UK visa enquiries. Returns the count processed."""
    messages = gmail.fetch_unread()
    processed = 0
    for message in messages:
        if not _looks_like_visa_enquiry(message):
            logger.debug(
                "Skipping email from %s (subject=%r) — doesn't look like a UK visa enquiry",
                message["from"], message["subject"],
            )
            continue

        db = get_session()
        try:
            logger.info(
                "Processing inbound email from %s: %r", message["from"], message["subject"],
            )
            _manager.handle_inbound_message(db, ChannelType.EMAIL, message["from"], message["body"])
            gmail.mark_read(message["uid"])
            processed += 1
        except Exception:
            logger.exception(
                "Failed to process email uid=%s from=%s — will retry next poll",
                message["uid"], message["from"],
            )
        finally:
            db.close()
    return processed


def run_forever(
    interval_seconds: int = EMAIL_POLL_INTERVAL_SECONDS,
    stop_event: threading.Event | None = None,
) -> None:
    """Blocking loop — call from a background thread, not the asyncio event
    loop. Runs until `stop_event` is set (or forever, if none is given)."""
    stop_event = stop_event or threading.Event()
    logger.info("Starting email poller (interval=%ds)", interval_seconds)
    while not stop_event.is_set():
        try:
            count = poll_once()
            if count:
                logger.info("Processed %d email(s)", count)
        except Exception:
            logger.exception("Email poll cycle failed")
        stop_event.wait(interval_seconds)
    logger.info("Email poller stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise SystemExit("GMAIL_USER and GMAIL_APP_PASSWORD must be set (see .env) to run the email poller.")
    init_db()
    run_forever()
