"""Tests for the cross-channel identity/case-linking behavior added in
MULTICHANNEL.md: a case is reachable from a second channel only after the
OTP challenge sent to the originating channel is answered correctly.
"""

import datetime
from pathlib import Path

from app.kb.retrieval import KnowledgeStore
from app.storage import repository
from app.storage.models import CaseStatus, ChannelType, IdentityRole
from app.workflow.linking import extract_case_reference, extract_otp_code, generate_case_reference
from app.workflow.orchestrator import Orchestrator

from tests.fake_llm import MultiToolScriptedLLM

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"

LOW_CONFIDENCE_ASSESSMENT = {
    "candidate_visa_types": [
        {"visa_type": "Standard Visitor", "likelihood": 0.5, "reasoning": "x"}
    ],
    "confidence": 0.3,
    "missing_info": [],
    "citations": [],
    "contradictions": [],
    "high_stakes_flags": [],
    "ready_for_determination": False,
    "next_question": "How long?",
    "reply_to_user": "How long do you plan to stay?",
}


class RecordingOtpSender:
    def __init__(self):
        self.sent: list[tuple] = []

    def __call__(self, identity, case_reference, code):
        self.sent.append((identity.channel, identity.address, case_reference, code))


def _orchestrator():
    kb = KnowledgeStore(kb_dir=FIXTURE_KB)
    # Enough scripted turns for a couple of low-confidence advisory rounds.
    llm = MultiToolScriptedLLM({"submit_visa_assessment": [dict(LOW_CONFIDENCE_ASSESSMENT) for _ in range(5)]})
    sender = RecordingOtpSender()
    return Orchestrator(llm, kb, otp_sender=sender), sender


def test_linking_helpers_round_trip():
    ref = generate_case_reference()
    assert extract_case_reference(f"my case is {ref}, please help") == ref
    assert extract_otp_code("the code is 048213 thanks") == "048213"
    assert extract_otp_code("no code here") is None


def test_new_whatsapp_message_starts_a_case(db):
    orchestrator, _ = _orchestrator()
    result = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "I'd like to visit the UK")

    assert "case_reference" in result
    case = repository.get_case_by_reference(db, result["case_reference"])
    assert case is not None
    identities = repository.get_linked_identities(db, case.id)
    assert len(identities) == 1
    assert identities[0].channel == ChannelType.WHATSAPP


def test_second_message_same_identity_continues_same_case(db):
    orchestrator, _ = _orchestrator()
    first = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi")
    second = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "still there")

    assert first["case_reference"] == second["case_reference"]


def test_case_reference_from_new_channel_triggers_otp_to_originating_channel(db):
    orchestrator, sender = _orchestrator()
    started = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi")
    reference = started["case_reference"]

    result = orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", f"my case is {reference}")

    assert result["status"] == "awaiting_verification"
    assert len(sender.sent) == 1
    channel, address, sent_ref, code = sender.sent[0]
    assert channel == ChannelType.WHATSAPP
    assert address == "+447700900123"
    assert sent_ref == reference
    assert len(code) == 6

    # Email identity is not yet linked to the case.
    case = repository.get_case_by_reference(db, reference)
    assert not any(
        i.channel == ChannelType.EMAIL for i in repository.get_linked_identities(db, case.id)
    )


def test_correct_otp_links_second_channel_and_shares_history(db):
    orchestrator, sender = _orchestrator()
    started = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi there")
    reference = started["case_reference"]
    orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", f"my case is {reference}")
    _, _, _, code = sender.sent[0]

    result = orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", f"the code is {code}")

    assert result["case_reference"] == reference
    case = repository.get_case_by_reference(db, reference)
    identities = repository.get_linked_identities(db, case.id)
    assert {i.channel for i in identities} == {ChannelType.WHATSAPP, ChannelType.EMAIL}

    roles = {
        link.identity.channel: link.role
        for link in case.identity_links
    }
    assert roles[ChannelType.WHATSAPP] == IdentityRole.ORIGINATING
    assert roles[ChannelType.EMAIL] == IdentityRole.LINKED

    # Both threads' messages are visible in the merged case history.
    history_channels = {m.conversation.identity.channel for m in repository.get_case_history(db, case.id)}
    assert history_channels == {ChannelType.WHATSAPP, ChannelType.EMAIL}

    # Now the email identity can carry on the case directly, landing in the same case.
    orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", "two weeks, tourism")
    updated_messages = [m.content for m in repository.get_case_history(db, case.id)]
    assert "two weeks, tourism" in updated_messages


def test_wrong_otp_does_not_link_and_can_be_retried(db):
    orchestrator, sender = _orchestrator()
    started = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi")
    reference = started["case_reference"]
    orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", f"my case is {reference}")

    result = orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", "the code is 000000")

    assert result["status"] == "verification_retry"
    case = repository.get_case_by_reference(db, reference)
    assert not any(
        i.channel == ChannelType.EMAIL for i in repository.get_linked_identities(db, case.id)
    )


def test_otp_exhausts_after_max_attempts(db):
    from app.config import MAX_OTP_ATTEMPTS

    orchestrator, sender = _orchestrator()
    started = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi")
    reference = started["case_reference"]
    orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", f"my case is {reference}")

    last = None
    for _ in range(MAX_OTP_ATTEMPTS):
        last = orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", "the code is 999999")

    assert last["status"] == "verification_expired"


def test_unknown_case_reference_gives_generic_reply(db):
    orchestrator, sender = _orchestrator()
    result = orchestrator.route_inbound(db, ChannelType.EMAIL, "nobody@example.com", "my case is VISA-ZZZZZ")

    assert result["status"] == "unlinked"
    assert sender.sent == []


def test_link_requests_are_rate_limited_per_case(db):
    from app.config import MAX_LINK_REQUESTS_PER_CASE_PER_HOUR

    orchestrator, sender = _orchestrator()
    started = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi")
    reference = started["case_reference"]

    results = []
    for i in range(MAX_LINK_REQUESTS_PER_CASE_PER_HOUR + 1):
        results.append(
            orchestrator.route_inbound(db, ChannelType.EMAIL, f"requester{i}@example.com", f"my case is {reference}")
        )

    assert results[-1]["status"] == "rate_limited"
    assert len(sender.sent) == MAX_LINK_REQUESTS_PER_CASE_PER_HOUR


def test_otp_sent_to_all_linked_identities_once_case_has_two_channels(db):
    orchestrator, sender = _orchestrator()
    started = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi")
    reference = started["case_reference"]
    orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", f"my case is {reference}")
    _, _, _, code = sender.sent[0]
    orchestrator.route_inbound(db, ChannelType.EMAIL, "jane@example.com", f"code {code}")
    sender.sent.clear()

    # A third channel now requests linking; OTP should go to both existing channels.
    orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900999", f"my case is {reference}")

    targeted_channels = {s[0] for s in sender.sent}
    assert targeted_channels == {ChannelType.WHATSAPP, ChannelType.EMAIL}


def test_identity_with_open_case_ignores_embedded_case_reference(db):
    """v1 scope decision (MULTICHANNEL.md §8): one open case per identity —
    an identity already in an open case is routed back into it rather than
    treated as a link attempt, even if their message happens to contain
    what looks like a case reference."""
    orchestrator, sender = _orchestrator()
    started = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900123", "hi")
    own_reference = started["case_reference"]
    other = orchestrator.route_inbound(db, ChannelType.WHATSAPP, "+447700900999", "hi")
    other_reference = other["case_reference"]

    orchestrator.route_inbound(
        db, ChannelType.WHATSAPP, "+447700900123", f"unrelated message mentioning {other_reference}"
    )

    own_case = repository.get_case_by_reference(db, own_reference)
    other_case = repository.get_case_by_reference(db, other_reference)
    own_messages = [m.content for m in repository.get_case_history(db, own_case.id)]
    other_messages = [m.content for m in repository.get_case_history(db, other_case.id)]

    assert any("unrelated message mentioning" in c for c in own_messages)
    assert not any("unrelated message mentioning" in c for c in other_messages)
    assert sender.sent == []
