from unittest.mock import MagicMock, patch

from app.messaging.twilio_client import _MAX_WHATSAPP_BODY_LENGTH, _split_message, send_whatsapp


def test_split_message_returns_body_unchanged_when_within_limit():
    assert _split_message("short reply") == ["short reply"]


def test_split_message_breaks_long_body_into_chunks_within_limit():
    body = "word " * 500  # well over 1600 chars
    chunks = _split_message(body)
    assert len(chunks) > 1
    assert all(len(chunk) <= _MAX_WHATSAPP_BODY_LENGTH for chunk in chunks)
    # No content lost or reordered across the split.
    assert " ".join(chunks).split() == body.split()


def test_split_message_prefers_paragraph_boundaries():
    para = "x" * 800
    body = f"{para}\n\n{para}\n\n{para}"
    chunks = _split_message(body)
    assert all(len(chunk) <= _MAX_WHATSAPP_BODY_LENGTH for chunk in chunks)
    assert all(chunk == para for chunk in chunks)


def test_send_whatsapp_sends_one_request_per_chunk():
    long_body = "word " * 500
    expected_chunks = _split_message(long_body)
    assert len(expected_chunks) > 1

    fake_response = MagicMock()
    fake_response.json.return_value = {"sid": "SMxxx", "status": "queued"}
    with patch("app.messaging.twilio_client.httpx.post", return_value=fake_response) as mock_post:
        results = send_whatsapp(to="+15551234567", body=long_body)

    assert mock_post.call_count == len(expected_chunks)
    assert len(results) == len(expected_chunks)
    sent_bodies = [call.kwargs["data"]["Body"] for call in mock_post.call_args_list]
    assert sent_bodies == expected_chunks
    assert all(len(b) <= _MAX_WHATSAPP_BODY_LENGTH for b in sent_bodies)
