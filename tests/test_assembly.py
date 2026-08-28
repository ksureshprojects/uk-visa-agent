from app.config import MAX_ASSEMBLY_BATCH_SIZE, MAX_VALIDATION_RETRIES
from app.storage import repository
from app.storage.models import ConversationStatus
from app.workflow.assembly import AssemblyEngine
from app.workflow.schema import Requirement, VisaSchema, load_schema

from tests.fake_llm import BatchScriptedLLM

# Standard Visitor requirements, in schema order: full_name, date_of_birth,
# nationality, passport_number, passport_expiry_date, purpose_of_visit,
# intended_arrival_date (7 = first batch) | intended_departure_date,
# accommodation_details, funds_declared_gbp, ties_to_home_country,
# passport_bio_page, bank_statements, proof_of_accommodation (7 = second
# batch, tourism path ends here) | invitation_letter, employer_letter
# (business/family_friends-only, third batch).
TOURIST_BATCHES = [
    {
        "full_name": "Jane Doe",
        "date_of_birth": "1990-05-01",
        "nationality": "Indian",
        "passport_number": "AB1234567",
        "passport_expiry_date": "2030-01-01",
        "purpose_of_visit": "tourism",
        "intended_arrival_date": "2027-01-10",
    },
    {
        "intended_departure_date": "2027-02-10",
        "accommodation_details": "Hilton Hotel, London",
        "funds_declared_gbp": "3000",
        "ties_to_home_country": "Full-time job as an engineer and owns an apartment in Mumbai",
        "passport_bio_page": "yes, scanned copy ready",
        "bank_statements": "yes, last 3 months ready",
        "proof_of_accommodation": "yes, booking confirmation ready",
    },
]

BUSINESS_BATCHES = [
    {**TOURIST_BATCHES[0], "purpose_of_visit": "business"},
    dict(TOURIST_BATCHES[1]),
    {
        "invitation_letter": "yes, invitation letter ready",
        "employer_letter": "yes, employer letter ready",
    },
]

# A minimal two-field schema reusing Standard Visitor's real cross-field
# validator, small enough that both fields always land in one batch —
# isolates the in-loop context-threading behavior from batch-size effects
# (Standard Visitor's own field ordering happens to split arrival/departure
# across two batches at MAX_ASSEMBLY_BATCH_SIZE=7).
ARRIVAL_DEPARTURE_SCHEMA = VisaSchema(
    visa_type="Test Arrival Departure",
    requirements=[
        Requirement(
            name="intended_arrival_date",
            kind="field",
            validator="iso_date_in_future",
            prompt="What date do they plan to arrive in the UK? Please give it as YYYY-MM-DD.",
        ),
        Requirement(
            name="intended_departure_date",
            kind="field",
            validator="departure_within_6_months_of_arrival",
            validator_args={"arrival_field": "intended_arrival_date"},
            prompt="What date do they plan to leave the UK? Please give it as YYYY-MM-DD.",
        ),
    ],
)


def _run_conversation(db, batches, visa_type="Standard Visitor"):
    schema = load_schema(visa_type)
    llm = BatchScriptedLLM([dict(b) for b in batches])
    engine = AssemblyEngine(llm, schema)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    result = None
    for _ in range(len(batches)):
        result = engine.handle_user_message(db, convo.id, "ignored, scripted")
        if result["done"]:
            break
    return engine, convo, result


def test_tourism_path_skips_business_only_documents_and_completes(db):
    _, convo, result = _run_conversation(db, TOURIST_BATCHES)

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


def test_tourism_path_takes_exactly_two_batches(db):
    schema = load_schema("Standard Visitor")
    llm = BatchScriptedLLM([dict(b) for b in TOURIST_BATCHES])
    engine = AssemblyEngine(llm, schema)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    first = engine.handle_user_message(db, convo.id, "ignored")
    assert first["done"] is False
    second = engine.handle_user_message(db, convo.id, "ignored")
    assert second["done"] is True


def test_business_path_requires_invitation_and_employer_letters(db):
    _, convo, result = _run_conversation(db, BUSINESS_BATCHES)

    assert result["done"] is True
    assert "invitation_letter" in result["package"]["documents_confirmed"]
    assert "employer_letter" in result["package"]["documents_confirmed"]


def test_conditional_documents_excluded_until_trigger_field_is_valid(db):
    # The core requirement: a question whose relevance depends on an
    # unanswered field must never appear in a batch before that field has a
    # *valid* answer — even with a batch limit large enough that size alone
    # wouldn't explain the exclusion.
    schema = load_schema("Standard Visitor")
    llm = BatchScriptedLLM([dict(BUSINESS_BATCHES[0])])
    engine = AssemblyEngine(llm, schema)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    before = {r.name for r in engine.next_batch(db, convo.id, limit=20)}
    assert "invitation_letter" not in before
    assert "employer_letter" not in before
    assert "purpose_of_visit" in before  # the trigger field itself must be askable

    engine.handle_user_message(db, convo.id, "ignored, scripted")  # answers purpose_of_visit="business" + others

    after = {r.name for r in engine.next_batch(db, convo.id, limit=20)}
    assert "invitation_letter" in after
    assert "employer_letter" in after


def test_first_batch_never_mentions_conditional_business_documents(db):
    schema = load_schema("Standard Visitor")
    engine = AssemblyEngine(BatchScriptedLLM([]), schema)
    convo = repository.create_conversation(db, "test-user")

    first_message = engine.start(db, convo.id)

    assert "invitation letter" not in first_message.lower()
    assert "employer" not in first_message.lower()
    assert "purpose of the visit" in first_message.lower()


def test_batch_never_exceeds_max_batch_size(db):
    schema = load_schema("Standard Visitor")
    engine = AssemblyEngine(BatchScriptedLLM([]), schema)
    convo = repository.create_conversation(db, "test-user")

    batch = engine.next_batch(db, convo.id)

    assert len(schema.requirements) > MAX_ASSEMBLY_BATCH_SIZE
    assert len(batch) == MAX_ASSEMBLY_BATCH_SIZE


def test_persistent_invalid_answers_keep_being_reprompted_instead_of_escalating(db):
    # There's no human caseworker to hand a stuck field off to in this
    # deployment — validation failures, however many, always just get
    # re-asked, never escalated. Every field in the first batch is left
    # blank/unanswered every turn, so the whole batch stays stuck together;
    # full_name (non_empty) is checked as representative.
    schema = load_schema("Standard Visitor")
    blank_batch = {"full_name": "   "}
    llm = BatchScriptedLLM([dict(blank_batch) for _ in range(MAX_VALIDATION_RETRIES + 2)])
    engine = AssemblyEngine(llm, schema)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    result = None
    for _ in range(MAX_VALIDATION_RETRIES + 2):
        result = engine.handle_user_message(db, convo.id, "ignored")
        assert result["done"] is False
        assert result["escalated"] is False

    refreshed = repository.get_conversation(db, convo.id)
    assert refreshed.status == ConversationStatus.ADVISORY
    assert refreshed.escalations == []
    field = next(f for f in repository.get_fields(db, convo.id) if f.field_name == "full_name")
    assert field.status == "invalid"
    assert field.retry_count == MAX_VALIDATION_RETRIES + 2
    assert "no rush" in result["reply_to_user"].lower()


def test_cross_field_validation_within_same_batch_sees_just_validated_value(db):
    # intended_departure_date's validator reads intended_arrival_date, which
    # is only committed to "valid" earlier in THIS SAME loop iteration (not
    # a prior turn) — proves the in-loop context threading in
    # AssemblyEngine.handle_user_message.
    llm = BatchScriptedLLM([
        {"intended_arrival_date": "2027-01-10", "intended_departure_date": "2027-03-01"},
    ])
    engine = AssemblyEngine(llm, ARRIVAL_DEPARTURE_SCHEMA)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    result = engine.handle_user_message(db, convo.id, "ignored, scripted")

    assert result["done"] is True
    fields = {f.field_name: f for f in repository.get_fields(db, convo.id)}
    assert fields["intended_arrival_date"].status == "valid"
    assert fields["intended_departure_date"].status == "valid"
    assert fields["intended_departure_date"].value == "2027-03-01"


def test_cross_field_departure_validation_rejects_over_6_months(db):
    llm = BatchScriptedLLM([
        {"intended_arrival_date": "2027-01-10", "intended_departure_date": "2027-12-01"},  # >6 months
        {"intended_departure_date": "2027-03-01"},  # corrected next turn
    ])
    engine = AssemblyEngine(llm, ARRIVAL_DEPARTURE_SCHEMA)
    convo = repository.create_conversation(db, "test-user")
    engine.start(db, convo.id)

    first = engine.handle_user_message(db, convo.id, "ignored")
    assert first["done"] is False
    result = engine.handle_user_message(db, convo.id, "ignored")

    assert result["done"] is True
    fields = {f.field_name: f for f in repository.get_fields(db, convo.id)}
    assert fields["intended_departure_date"].status == "valid"
    assert fields["intended_departure_date"].value == "2027-03-01"
