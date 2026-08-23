"""Deterministic field validators for Phase 2.

Every function here has the signature (raw_value, context, args) ->
ValidationResult. `context` is the map of already-validated field values for
this conversation, so cross-field checks (e.g. departure vs arrival date)
are possible without the validator reaching back into the database itself —
keeps these pure and directly unit-testable.

A field is marked "valid" in storage only when one of these functions
returns ok=True. The LLM never marks a field complete on its own — see
app/workflow/assembly.py.
"""

import datetime
import re
from dataclasses import dataclass
from typing import Any, Callable

PASSPORT_RE = re.compile(r"^[A-Za-z0-9]{5,12}$")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    error: str | None = None
    normalized_value: str | None = None


def _parse_iso_date(raw: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(raw.strip())
    except (ValueError, AttributeError):
        return None


def non_empty(raw: str | None, context: dict, args: dict) -> ValidationResult:
    if raw is None or not raw.strip():
        return ValidationResult(False, "That doesn't look like an answer — could you provide it?")
    return ValidationResult(True, normalized_value=raw.strip())


def min_length_20(raw: str | None, context: dict, args: dict) -> ValidationResult:
    if raw is None or len(raw.strip()) < 20:
        return ValidationResult(
            False, "Could you give a bit more detail (at least a sentence)?"
        )
    return ValidationResult(True, normalized_value=raw.strip())


def passport_number(raw: str | None, context: dict, args: dict) -> ValidationResult:
    if raw is None or not PASSPORT_RE.match(raw.strip()):
        return ValidationResult(
            False, "That doesn't look like a valid passport number (expected 5-12 letters/digits)."
        )
    return ValidationResult(True, normalized_value=raw.strip().upper())


def positive_number(raw: str | None, context: dict, args: dict) -> ValidationResult:
    if raw is None:
        return ValidationResult(False, "Please give an approximate amount in GBP.")
    cleaned = re.sub(r"[^0-9.]", "", raw)
    try:
        value = float(cleaned)
    except ValueError:
        return ValidationResult(False, "Please give an amount as a number, e.g. 2500.")
    if value <= 0:
        return ValidationResult(False, "The amount must be greater than zero.")
    return ValidationResult(True, normalized_value=str(value))


def enum_value(raw: str | None, context: dict, args: dict) -> ValidationResult:
    options = args["options"]
    if raw is None:
        return ValidationResult(False, f"Please choose one of: {', '.join(options)}.")
    normalized = raw.strip().lower().replace(" ", "_")
    if normalized not in options:
        return ValidationResult(False, f"Please choose one of: {', '.join(options)}.")
    return ValidationResult(True, normalized_value=normalized)


def iso_date_in_past(raw: str | None, context: dict, args: dict) -> ValidationResult:
    date = _parse_iso_date(raw) if raw else None
    if date is None:
        return ValidationResult(False, "Please give a date in YYYY-MM-DD format.")
    if date >= datetime.date.today():
        return ValidationResult(False, "That date should be in the past.")
    return ValidationResult(True, normalized_value=date.isoformat())


def iso_date_in_future(raw: str | None, context: dict, args: dict) -> ValidationResult:
    date = _parse_iso_date(raw) if raw else None
    if date is None:
        return ValidationResult(False, "Please give a date in YYYY-MM-DD format.")
    if date <= datetime.date.today():
        return ValidationResult(False, "That date should be in the future.")
    return ValidationResult(True, normalized_value=date.isoformat())


def departure_within_6_months_of_arrival(raw: str | None, context: dict, args: dict) -> ValidationResult:
    departure = _parse_iso_date(raw) if raw else None
    if departure is None:
        return ValidationResult(False, "Please give a date in YYYY-MM-DD format.")

    arrival_field = args["arrival_field"]
    arrival_raw = context.get(arrival_field)
    arrival = _parse_iso_date(arrival_raw) if arrival_raw else None
    if arrival is None:
        # Arrival date isn't validated yet; the schema orders arrival before
        # departure so this shouldn't happen in normal flow, but fail closed.
        return ValidationResult(False, "I need the arrival date confirmed first.")

    if departure <= arrival:
        return ValidationResult(False, "The departure date must be after the arrival date.")
    if (departure - arrival).days > 186:
        return ValidationResult(
            False,
            "A Standard Visitor stay normally cannot exceed 6 months — that gap is longer than that.",
        )
    return ValidationResult(True, normalized_value=departure.isoformat())


VALIDATORS: dict[str, Callable[[str | None, dict, dict], ValidationResult]] = {
    "non_empty": non_empty,
    "min_length_20": min_length_20,
    "passport_number": passport_number,
    "positive_number": positive_number,
    "enum_value": enum_value,
    "iso_date_in_past": iso_date_in_past,
    "iso_date_in_future": iso_date_in_future,
    "departure_within_6_months_of_arrival": departure_within_6_months_of_arrival,
}


def validate(name: str, raw_value: str | None, context: dict[str, str], args: dict[str, Any] | None = None) -> ValidationResult:
    return VALIDATORS[name](raw_value, context, args or {})
