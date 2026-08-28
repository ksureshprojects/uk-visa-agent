import re
import threading
from pathlib import Path
from unittest.mock import patch

from app.identity import case_locks
from app.identity.session_manager import IdentitySessionManager
from app.kb.retrieval import KnowledgeStore
from app.storage.models import ChannelType
from app.workflow.orchestrator import Orchestrator

from tests.fake_llm import MultiToolScriptedLLM

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"

CONFIDENT_ASSESSMENT = {
    "candidate_visa_types": [
        {"visa_type": "Standard Visitor", "likelihood": 0.9, "reasoning": "Tourism, short stay, funds confirmed."}
    ],
    "confidence": 0.9,
    "missing_info": [],
    "citations": ["fixture-financial-01"],
    "contradictions": [],
    "high_stakes_flags": [],
    "ready_for_determination": True,
    "next_question": None,
    "reply_to_user": "This looks like a Standard Visitor Visa case.",
}

# Standard Visitor's 14 tourism-path requirements, split into the two
# 7-field batches app/workflow/assembly.py asks for (see tests/test_assembly.py).
BATCH_1_ANSWERS = {
    "full_name": "Jane Doe",
    "date_of_birth": "1990-05-01",
    "nationality": "Indian",
    "passport_number": "AB1234567",
    "passport_expiry_date": "2030-01-01",
    "purpose_of_visit": "tourism",
    "intended_arrival_date": "2027-01-10",
}
BATCH_2_ANSWERS = {
    "intended_departure_date": "2027-02-10",
    "accommodation_details": "Hilton Hotel, London",
    "funds_declared_gbp": "3000",
    "ties_to_home_country": "Full-time job as an engineer and owns a flat in Mumbai",
    "passport_bio_page": "yes, scanned copy ready",
    "bank_statements": "yes, last 3 months ready",
    "proof_of_accommodation": "yes, booking confirmation ready",
}


def _manager():
    kb = KnowledgeStore(kb_dir=FIXTURE_KB)
    llm = MultiToolScriptedLLM(
        {
            "submit_visa_assessment": [dict(CONFIDENT_ASSESSMENT)],
            "extract_field_values": [dict(BATCH_1_ANSWERS), dict(BATCH_2_ANSWERS)],
        }
    )
    return IdentitySessionManager(orchestrator=Orchestrator(llm, kb))


def test_completing_phase_2_emails_a_package_summary(db):
    manager = _manager()
    sent_emails = []

    def fake_send_email(to, subject, body):
        sent_emails.append({"to": to, "subject": subject, "body": body})

    with patch("app.identity.session_manager.gmail.send_email", side_effect=fake_send_email):
        manager.handle_inbound_message(
            db, ChannelType.EMAIL, "traveler@example.com", "I want to visit the UK as a tourist"
        )
        manager.handle_inbound_message(db, ChannelType.EMAIL, "traveler@example.com", "here's my first batch")
        result = manager.handle_inbound_message(
            db, ChannelType.EMAIL, "traveler@example.com", "here's the rest"
        )

    # The email channel also sends every turn's *primary* reply via
    # gmail.send_email (subject="UK Visa Agent") — the package summary is
    # the one additional, distinctly-subjected email sent alongside those.
    summary_emails = [e for e in sent_emails if "application summary" in e["subject"]]
    assert len(summary_emails) == 1
    assert summary_emails[0]["to"] == "traveler@example.com"
    assert "Full name: Jane Doe" in summary_emails[0]["body"]

    # The turn's primary reply (what the user actually reads back) should
    # tell them where the full package went, since it isn't inlined here.
    assert "traveler@example.com" in result["reply"]
    assert "draft" in summary_emails[0]["body"].lower()


def test_package_summary_is_not_sent_before_phase_2_completes(db):
    manager = _manager()
    sent_emails = []

    def fake_send_email(to, subject, body):
        sent_emails.append({"to": to, "subject": subject, "body": body})

    with patch("app.identity.session_manager.gmail.send_email", side_effect=fake_send_email):
        manager.handle_inbound_message(
            db, ChannelType.EMAIL, "traveler@example.com", "I want to visit the UK as a tourist"
        )
        manager.handle_inbound_message(db, ChannelType.EMAIL, "traveler@example.com", "here's my first batch")

    assert not any("application summary" in e["subject"] for e in sent_emails)


def test_package_summary_email_failure_does_not_break_the_turn(db):
    manager = _manager()

    def failing_send_email(to, subject, body):
        if "application summary" in subject:
            raise RuntimeError("SMTP down")

    with patch("app.identity.session_manager.gmail.send_email", side_effect=failing_send_email):
        manager.handle_inbound_message(
            db, ChannelType.EMAIL, "traveler2@example.com", "I want to visit the UK as a tourist"
        )
        manager.handle_inbound_message(db, ChannelType.EMAIL, "traveler2@example.com", "here's my first batch")
        result = manager.handle_inbound_message(
            db, ChannelType.EMAIL, "traveler2@example.com", "here's the rest"
        )

    assert result["state"] == "active"
    assert "draft application package" in result["reply"].lower()
    assert "traveler2@example.com" not in result["reply"]


def test_existing_case_choice_with_no_cases_creates_new_case_immediately(db):
    """Regression test: a WhatsApp user with no existing cases who says
    "existing case" used to land in AWAITING_CASE_REFERENCE and be asked for
    a case id. Any reply that wasn't a real case id (e.g. "Ok new case")
    failed the lookup and re-asked forever, with no way out. Now a "no cases
    found" response should just start a new case immediately."""
    manager = _manager()
    phone = "+15551234567"

    with patch("app.identity.session_manager.twilio_client.send_whatsapp"), \
         patch("app.identity.session_manager.gmail.send_email") as mock_email:
        manager.handle_inbound_message(db, ChannelType.WHATSAPP, phone, "I want to visit the UK")
        manager.handle_inbound_message(db, ChannelType.WHATSAPP, phone, "traveler@example.com")

        otp_body = mock_email.call_args.kwargs["body"]
        code = re.search(r"\d{6}", otp_body).group()
        manager.handle_inbound_message(db, ChannelType.WHATSAPP, phone, code)

        result = manager.handle_inbound_message(db, ChannelType.WHATSAPP, phone, "existing case")

    assert result["state"] == "active"
    assert "couldn't find any existing cases" in result["reply"].lower()
    assert "started a new one" in result["reply"].lower()


def test_new_case_can_be_started_after_previous_case_completes(db):
    """Regression test: once a case reaches READY_FOR_HUMAN_REVIEW, the
    session stayed ACTIVE against it forever — every further message,
    including "New case", was routed straight to the finished case's
    advisory pipeline, which just repeats a static "already complete" reply
    no matter what's said."""
    kb = KnowledgeStore(kb_dir=FIXTURE_KB)
    llm = MultiToolScriptedLLM(
        {
            "submit_visa_assessment": [dict(CONFIDENT_ASSESSMENT), dict(CONFIDENT_ASSESSMENT)],
            "extract_field_values": [dict(BATCH_1_ANSWERS), dict(BATCH_2_ANSWERS)],
        }
    )
    manager = IdentitySessionManager(orchestrator=Orchestrator(llm, kb))
    email = "traveler4@example.com"

    with patch("app.identity.session_manager.gmail.send_email"):
        manager.handle_inbound_message(db, ChannelType.EMAIL, email, "I want to visit the UK as a tourist")
        manager.handle_inbound_message(db, ChannelType.EMAIL, email, "here's my first batch")
        first = manager.handle_inbound_message(db, ChannelType.EMAIL, email, "here's the rest")
        assert "draft application package" in first["reply"].lower()

        second = manager.handle_inbound_message(db, ChannelType.EMAIL, email, "New case")

    assert second["state"] == "active"
    assert "started new case" in second["reply"].lower()
    assert second["reply"] != first["reply"]


def test_active_case_turns_acquire_the_per_case_lock(db):
    """Wiring check: once a session is tied to a case, handle_inbound_message
    should serialize that turn through case_locks.lock_for_case(case_id) —
    this is what actually prevents a WhatsApp message and an inbound email
    for the same case from being advanced by two threads at once."""
    manager = _manager()

    with patch(
        "app.identity.session_manager.case_locks.lock_for_case", side_effect=case_locks.lock_for_case
    ) as mock_lock_for_case, patch("app.identity.session_manager.gmail.send_email"):
        # First message: no case yet (still identity verification) — must
        # not touch the per-case lock at all.
        manager.handle_inbound_message(
            db, ChannelType.EMAIL, "locktest@example.com", "I want to visit the UK as a tourist"
        )
        mock_lock_for_case.assert_not_called()

        # Second message: a case now exists and is linked to the session —
        # this turn must be locked by that case's id.
        result = manager.handle_inbound_message(
            db, ChannelType.EMAIL, "locktest@example.com", "here's my first batch"
        )

    mock_lock_for_case.assert_called_once()
    (locked_case_id,), _ = mock_lock_for_case.call_args
    assert locked_case_id  # a real case id, not None/empty


def test_two_threads_on_the_same_case_do_not_run_concurrently(tmp_path):
    """End-to-end version of the case_locks guarantee: two threads turning
    the same already-active case (simulating a WhatsApp reply and an
    inbound email landing at the same moment) must not overlap. Uses a
    file-backed sqlite DB with one Session per thread — the in-memory `db`
    fixture is a single Session, which isn't thread-safe to share, so it
    would risk flaking for reasons unrelated to the lock under test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.storage.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    manager = _manager()
    overlap_detected = threading.Event()
    in_flight = threading.Event()
    real_lock_for_case = case_locks.lock_for_case

    def spying_lock_for_case(case_id):
        lock = real_lock_for_case(case_id)

        class _SpyLock:
            def __enter__(self):
                lock.acquire()
                if in_flight.is_set():
                    overlap_detected.set()
                in_flight.set()
                return self

            def __exit__(self, *exc):
                in_flight.clear()
                lock.release()

        return _SpyLock()

    def run_turn():
        thread_db = Session()
        try:
            manager.handle_inbound_message(
                thread_db, ChannelType.EMAIL, "concurrent@example.com", "here's my first batch"
            )
        finally:
            thread_db.close()

    with patch("app.identity.session_manager.gmail.send_email"):
        setup_db = Session()
        manager.handle_inbound_message(
            setup_db, ChannelType.EMAIL, "concurrent@example.com", "I want to visit the UK as a tourist"
        )
        setup_db.close()

        with patch(
            "app.identity.session_manager.case_locks.lock_for_case", side_effect=spying_lock_for_case
        ):
            threads = [threading.Thread(target=run_turn) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

    assert not overlap_detected.is_set()
