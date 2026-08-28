from unittest.mock import patch

from app.messaging.email_poller import poll_once


def test_poll_once_asks_gmail_for_a_server_side_uk_visa_prefilter():
    """Regression test: fetch_unread() used to be called with no filter,
    downloading every UNSEEN message's full body (hundreds, in a real
    mailbox with a backlog) before this module's own regex filter ever
    ran — making a poll cycle take minutes. It must now pass the same
    uk/visa terms as a server-side pre-filter."""
    with patch("app.messaging.email_poller.gmail.fetch_unread", return_value=[]) as mock_fetch:
        poll_once()

    mock_fetch.assert_called_once_with(text_filter_terms=["uk", "visa"])
