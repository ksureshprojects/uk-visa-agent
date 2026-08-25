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

# Cross-channel case linking (MULTICHANNEL.md §6-7)
CASE_REFERENCE_PREFIX = "VISA"
OTP_TTL_MINUTES = 10
MAX_OTP_ATTEMPTS = 5
MAX_LINK_REQUESTS_PER_CASE_PER_HOUR = 3
