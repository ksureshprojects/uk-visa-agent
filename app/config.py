import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
KB_DIR = DATA_DIR / "kb"
SCHEMAS_DIR = DATA_DIR / "schemas"
DB_PATH = BASE_DIR / "visa_agent.db"
# Local retrieval embedding model (fastembed/onnxruntime) is downloaded once
# on first run and cached here — pinned to a project path rather than
# fastembed's /tmp default so a container restart doesn't force a re-download.
EMBEDDING_MODEL_CACHE_DIR = BASE_DIR / ".model_cache"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Only required for "identity-linked" API keys (Console keys tied to a user
# rather than a workspace) — the API rejects those with a 400 unless every
# request also carries the workspace it should act in.
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "anthropic")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Checkpoint gate thresholds. The gate no longer escalates to a human when
# rounds run out (it force-passes with a best-effort determination instead,
# see app/workflow/gate.py) — every visa category now also has a Phase 2
# assembly schema, so a forced pass always lands the applicant in Assembly
# rather than looping the same advisory turn. MAX_CLARIFY_ROUNDS=3 pairs with
# the advisory system prompt's guidance to batch up to 3 related questions
# per round, so each round earns real ground toward a confident assessment
# rather than trickling one fact at a time. RETRIEVAL_TOP_K is higher than a
# single-visa-type KB would need, since the corpus now spans multiple visa
# categories.
CONFIDENCE_THRESHOLD = 0.75
MAX_CLARIFY_ROUNDS = 3
MAX_VALIDATION_RETRIES = 3
RETRIEVAL_TOP_K = 6

# Phase 2 batches up to this many still-applicable requirements into one
# outbound message rather than asking one field at a time. A requirement
# whose Condition depends on a field that isn't answered yet never enters a
# batch in the first place (see Requirement.applies in app/workflow/schema.py),
# so this only ever groups questions that are all genuinely askable right now.
MAX_ASSEMBLY_BATCH_SIZE = 7

# Identity verification / case management
SESSION_IDLE_TIMEOUT_MINUTES = 30
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

# Twilio (WhatsApp via Messaging API, email via SendGrid Inbound Parse / Mail Send)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")  # e.g. "+14155238886"
TWILIO_SENDGRID_API_KEY = os.environ.get("TWILIO_SENDGRID_API_KEY", "")
TWILIO_SENDGRID_FROM_EMAIL = os.environ.get("TWILIO_SENDGRID_FROM_EMAIL", "")

# Gmail (email channel: IMAP polling for inbound, SMTP for outbound — see
# app/messaging/gmail.py). GMAIL_APP_PASSWORD is a Google "app password",
# not the account password: https://myaccount.google.com/apppasswords
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
EMAIL_POLL_INTERVAL_SECONDS = int(os.environ.get("EMAIL_POLL_INTERVAL_SECONDS", "15"))
