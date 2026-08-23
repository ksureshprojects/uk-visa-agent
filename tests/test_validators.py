from app.workflow.validators import validate


def test_non_empty_rejects_blank():
    assert not validate("non_empty", "   ", {}).ok


def test_passport_number_format():
    assert validate("passport_number", "AB1234567", {}).ok
    assert not validate("passport_number", "!!", {}).ok


def test_positive_number_parses_currency_noise():
    result = validate("positive_number", "£2,500", {})
    assert result.ok
    assert result.normalized_value == "2500.0"


def test_positive_number_rejects_zero():
    assert not validate("positive_number", "0", {}).ok


def test_enum_value_normalizes_and_rejects_unknown():
    args = {"options": ["tourism", "business"]}
    assert validate("enum_value", "Tourism", {}, args).ok
    assert not validate("enum_value", "vacation", {}, args).ok


def test_iso_date_in_future_rejects_past():
    assert not validate("iso_date_in_future", "2020-01-01", {}).ok


def test_departure_requires_arrival_in_context_and_within_6_months():
    args = {"arrival_field": "intended_arrival_date"}

    # arrival not yet known -> fail closed
    assert not validate("departure_within_6_months_of_arrival", "2027-01-01", {}, args).ok

    context = {"intended_arrival_date": "2027-01-01"}
    assert validate("departure_within_6_months_of_arrival", "2027-03-01", context, args).ok
    assert not validate("departure_within_6_months_of_arrival", "2027-12-01", context, args).ok
    assert not validate("departure_within_6_months_of_arrival", "2026-12-01", context, args).ok  # before arrival
