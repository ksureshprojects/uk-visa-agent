"""Pure helpers for the cross-channel case-linking flow (MULTICHANNEL.md §6):
case reference generation/parsing, OTP generation/hashing/parsing. Kept
free of any DB or transport dependency, same reasoning as gate.py — the
rules here are deterministic and independently testable.
"""

import hashlib
import re
import secrets

from app.config import CASE_REFERENCE_PREFIX

# Crockford base32: excludes I, L, O, U to avoid confusion with 1/1/0/V.
_REFERENCE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_REFERENCE_SUFFIX_LEN = 5

CASE_REFERENCE_RE = re.compile(
    rf"\b{re.escape(CASE_REFERENCE_PREFIX)}-[0-9A-HJKMNP-TV-Z]{{{_REFERENCE_SUFFIX_LEN}}}\b"
)
OTP_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def generate_case_reference() -> str:
    suffix = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(_REFERENCE_SUFFIX_LEN))
    return f"{CASE_REFERENCE_PREFIX}-{suffix}"


def extract_case_reference(text: str) -> str | None:
    match = CASE_REFERENCE_RE.search(text.upper())
    return match.group(0) if match else None


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def extract_otp_code(text: str) -> str | None:
    match = OTP_RE.search(text)
    return match.group(1) if match else None


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()
