import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
KB_DIR = DATA_DIR / "kb"
SCHEMAS_DIR = DATA_DIR / "schemas"
DB_PATH = BASE_DIR / "visa_agent.db"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "anthropic")

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
