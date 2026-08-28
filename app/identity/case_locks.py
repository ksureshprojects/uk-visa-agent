"""In-process locks keyed by case id.

The email poller (app/messaging/email_poller.py) runs in its own background
thread, fully independent of the FastAPI webhook server's request threads
(app/api/webhooks.py's handlers are sync `def`s, so Starlette dispatches
each inbound webhook to its own thread-pool thread too) — so a WhatsApp
message and an inbound email can genuinely be mid-processing at the same
instant. If they resolve to the same case, letting both run the advisory/
assembly pipeline concurrently against that case's conversation would race:
duplicate answers to the same batch, double-incremented clarify rounds,
etc. Different cases have no shared state and should keep processing fully
in parallel, so the lock is per-case, not global or per-user.
"""

import threading
from collections import defaultdict

_registry_lock = threading.Lock()
_case_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def lock_for_case(case_id: str) -> threading.Lock:
    with _registry_lock:
        return _case_locks[case_id]
