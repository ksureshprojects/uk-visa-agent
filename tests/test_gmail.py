from unittest.mock import MagicMock, patch

from app.messaging.gmail import fetch_unread


def _fake_imap(search_result=(b"",)):
    fake = MagicMock()
    fake.__enter__.return_value = fake
    fake.search.return_value = ("OK", [b""])
    fake.fetch.return_value = ("OK", [(b"1", b"Subject: test\r\n\r\nbody")])
    return fake


def test_fetch_unread_with_no_filter_terms_searches_unseen_only():
    fake = _fake_imap()
    with patch("app.messaging.gmail.imaplib.IMAP4_SSL", return_value=fake):
        fetch_unread()

    fake.search.assert_called_once_with(None, "UNSEEN")


def test_fetch_unread_with_filter_terms_adds_server_side_text_search():
    fake = _fake_imap()
    with patch("app.messaging.gmail.imaplib.IMAP4_SSL", return_value=fake):
        fetch_unread(text_filter_terms=["uk", "visa"])

    fake.search.assert_called_once_with(None, "UNSEEN", "TEXT", "uk", "TEXT", "visa")
