from app.config import MAX_VALIDATION_RETRIES
from app.storage import repository
from app.storage.models import ConversationStatus
from app.workflow.assembly import AssemblyEngine
from app.workflow.schema import load_schema

from tests.fake_llm import ScriptedLLM

TOURIST_ANSWERS = [
    "Jane Doe",  # full_name
    "1990-05-01",  # date_of_birth
    "Indian",  # nationality
    "AB1234567",  # passport_number
    "2030-01-01",  # passport_expiry_date
    "tourism",  # purpose_of_visit
    "2027-01-10",  # intended_arrival_date
    "2027-02-10",  # intended_departure_date
    "Hilton Hotel, London",  # accommodation_details
    "3000",  # funds_declared_gbp
    "Full-time job as an engineer and owns an apartment in Mumbai",  # ties_to_home_country
    "yes, scanned copy ready",  # passport_bio_page
    "yes, last 3 months ready",  # bank_statements
    "yes, booking confirmation ready",  # proof_of_accommodation
]


def _run_conversation(db, answers):
    schema = load_schema("Standard Visitor")
    llm = ScriptedLLM(answers)
    engine = AssemblyEngine(llm, schema)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    result = None
    for _ in range(len(answers)):
        result = engine.handle_user_message(db, convo.id, "ignored, scripted")
        if result["done"]:
            break
    return engine, convo, result


def test_tourism_path_skips_business_only_documents_and_completes(db):
    _, convo, result = _run_conversation(db, TOURIST_ANSWERS)

    assert result["done"] is True
    assert result["escalated"] is False
    package = result["package"]
    assert package["fields"]["full_name"] == "Jane Doe"
    assert package["fields"]["purpose_of_visit"] == "tourism"
    assert set(package["documents_confirmed"]) == {
        "passport_bio_page",
        "bank_statements",
        "proof_of_accommodation",
    }
    assert "invitation_letter" not in package["documents_confirmed"]


def test_business_path_requires_invitation_and_employer_letters(db):
    business_answers = list(TOURIST_ANSWERS)
    business_answers[5] = "business"  # purpose_of_visit
    business_answers += ["yes, invitation letter ready", "yes, employer letter ready"]

    _, convo, result = _run_conversation(db, business_answers)

    assert result["done"] is True
    assert "invitation_letter" in result["package"]["documents_confirmed"]
    assert "employer_letter" in result["package"]["documents_confirmed"]


def test_persistent_invalid_answers_escalate_instead_of_looping_forever(db):
    schema = load_schema("Standard Visitor")
    # full_name only needs non_empty, so we hit it with blanks repeatedly.
    answers = ["   "] * (MAX_VALIDATION_RETRIES + 1)
    llm = ScriptedLLM(answers)
    engine = AssemblyEngine(llm, schema)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    result = None
    for _ in range(MAX_VALIDATION_RETRIES):
        result = engine.handle_user_message(db, convo.id, "ignored")

    assert result["escalated"] is True
    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.status == ConversationStatus.NEEDS_HUMAN_REVIEW
    assert len(refreshed.escalations) == 1
    assert refreshed.escalations[0].trigger == "persistent_validation_failure"


def test_cross_field_departure_validation_rejects_over_6_months(db):
    answers = list(TOURIST_ANSWERS)
    answers[7] = "2027-12-01"  # intended_departure_date, >6 months after 2027-01-10
    # Provide one more (valid) attempt after the rejection so the flow can proceed.
    answers.insert(8, "2027-03-01")

    schema = load_schema("Standard Visitor")
    llm = ScriptedLLM(answers)
    engine = AssemblyEngine(llm, schema)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    for _ in range(9):  # 7 fields up to arrival, 1 rejected departure attempt, 1 corrected departure
        result = engine.handle_user_message(db, convo.id, "ignored")

    fields = {f.field_name: f for f in repository.get_fields(db, convo.id)}
    assert fields["intended_departure_date"].status == "valid"
    assert fields["intended_departure_date"].value == "2027-03-01"
