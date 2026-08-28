"""Gmail client for the email channel: sends via SMTP and polls for inbound
messages via IMAP (Gmail has no inbound webhook, so app/messaging/email_poller.py
polls this module on an interval). Uses only the standard library
(smtplib, imaplib, email) against GMAIL_USER / GMAIL_APP_PASSWORD
(app/config.py).

SETUP:
1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
3. Set GMAIL_USER and GMAIL_APP_PASSWORD in .env.
"""

import email
import imaplib
import logging
import smtplib
from email.header import decode_header
from email.message import Message
from email.mime.text import MIMEText
from typing import Any

from app.config import GMAIL_APP_PASSWORD, GMAIL_USER

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email via Gmail's SMTP server."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = to

    logger.debug("Sending email to %s (subject=%r)", to, subject)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [to], msg.as_string())
    logger.info("Email sent to %s (subject=%r)", to, subject)


def fetch_unread(text_filter_terms: list[str] | None = None) -> list[dict[str, Any]]:
    """Return unread inbox messages as a list of
    {"uid": bytes, "from": "sender@example.com", "subject": str, "body": str}.

    Uses BODY.PEEK[] rather than a plain FETCH so messages are *not* marked
    read as a side effect of looking at them — callers should call
    mark_read(uid) themselves once a message has been successfully processed,
    so a failure can be retried on the next poll.

    text_filter_terms, if given, narrows the UNSEEN search server-side with
    an IMAP TEXT search per term (ANDed together, matching subject or body)
    before any message body is downloaded. This is a coarse, best-effort
    pre-filter only — a mailbox can have hundreds of UNSEEN messages
    (newsletters etc.) that have nothing to do with what the caller wants,
    and fetching every one's full body first is what makes a poll cycle
    take minutes instead of seconds. Callers that need exact matching
    (e.g. whole-word regex) should still re-check the messages returned
    here themselves — Gmail's TEXT search isn't guaranteed to have
    identical semantics.
    """
    messages: list[dict[str, Any]] = []
    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        imap.select("INBOX")

        criteria = ["UNSEEN"]
        for term in text_filter_terms or []:
            criteria += ["TEXT", term]
        _, uids = imap.search(None, *criteria)
        for uid in uids[0].split():
            _, msg_data = imap.fetch(uid, "(BODY.PEEK[])")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject, encoding = decode_header(msg["Subject"] or "")[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="replace")

            messages.append(
                {
                    "uid": uid,
                    "from": _extract_address(msg.get("From", "")),
                    "subject": subject or "",
                    "body": _plain_text_body(msg),
                }
            )
    logger.debug("Fetched %d unread message(s)", len(messages))
    return messages


def mark_read(uid: bytes) -> None:
    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        imap.store(uid, "+FLAGS", "\\Seen")


def _extract_address(from_header: str) -> str:
    """`From` is a full header value, e.g. '"Jane Doe" <jane@example.com>' —
    pull out just the address."""
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0].strip().lower()
    return from_header.strip().lower()


def _plain_text_body(msg: Message) -> str:
    if not msg.is_multipart():
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        return payload.decode(charset, errors="replace") if payload else ""

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        if part.get_content_type() == "text/plain" and "attachment" not in disposition:
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            return payload.decode(charset, errors="replace") if payload else ""
    return ""


if __name__ == "__main__":
    # Manual smoke test — edit the recipient as needed.
    send_email(
        to=GMAIL_USER,
        subject="Test from Python",
        body="Hello there! This was sent from the Gmail client module.",
    )

    print("\nUnread inbox messages:\n")
    for message in fetch_unread():
        print(f"From: {message['from']}")
        print(f"Subject: {message['subject']}")
        print(f"Body: {message['body'][:200]!r}")
        print("-" * 40)
