import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
KB_DIR = DATA_DIR / "kb"
SCHEMAS_DIR = DATA_DIR / "schemas"
DB_PATH = BASE_DIR / "visa_agent.db"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "anthropic")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Checkpoint gate thresholds
CONFIDENCE_THRESHOLD = 0.75
MAX_CLARIFY_ROUNDS = 3
MAX_VALIDATION_RETRIES = 3
RETRIEVAL_TOP_K = 4

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
