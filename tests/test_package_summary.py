from app.messaging.package_summary import format_package_email

PACKAGE = {
    "visa_type": "Standard Visitor",
    "fields": {"full_name": "Jane Doe", "purpose_of_visit": "tourism"},
    "documents_confirmed": ["passport_bio_page", "bank_statements"],
    "status": "draft_ready_for_human_review",
}


def test_subject_includes_visa_type_and_case_id():
    subject, _ = format_package_email("case-123", PACKAGE)

    assert "Standard Visitor" in subject
    assert "case-123" in subject


def test_body_lists_every_field_with_a_readable_label():
    _, body = format_package_email("case-123", PACKAGE)

    assert "Full name: Jane Doe" in body
    assert "Purpose of visit: tourism" in body


def test_body_lists_confirmed_documents():
    _, body = format_package_email("case-123", PACKAGE)

    assert "Passport bio page" in body
    assert "Bank statements" in body


def test_body_notes_no_documents_when_none_confirmed():
    package = {**PACKAGE, "documents_confirmed": []}

    _, body = format_package_email("case-123", package)

    assert "(none)" in body


def test_body_caveats_draft_only():
    _, body = format_package_email("case-123", PACKAGE)

    assert "draft" in body.lower()
    assert "caseworker" in body.lower()
