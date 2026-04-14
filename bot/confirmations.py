"""TTL-based store for inline confirmation buttons (push and PR)."""

import time
import uuid
from dataclasses import dataclass
from threading import Lock


TTL_SECONDS = 120  # confirmations expire after 2 minutes


@dataclass
class PendingConfirmation:
    """A destructive action waiting on a user button press."""

    action: str            # "push" or "pr"
    agent_name: str
    chat_id: int
    payload: str           # empty for push, PR title for pr
    expires_at: float


class ConfirmationStore:
    """Hold short-lived pending confirmations keyed by callback id."""

    def __init__(self):
        self._store: dict[str, PendingConfirmation] = {}
        self._lock = Lock()

    def create(
        self,
        action: str,
        agent_name: str,
        chat_id: int,
        payload: str = "",
    ) -> str:
        """Register a pending confirmation and return its callback id."""

        token = f"cfrm_{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._purge_expired_locked()
            self._store[token] = PendingConfirmation(
                action=action,
                agent_name=agent_name,
                chat_id=chat_id,
                payload=payload,
                expires_at=time.time() + TTL_SECONDS,
            )
        return token

    def pop(self, token: str) -> PendingConfirmation | None:
        """Retrieve and remove a confirmation, or None if missing/expired."""

        with self._lock:
            self._purge_expired_locked()
            return self._store.pop(token, None)

    def _purge_expired_locked(self):
        """Drop expired entries. Caller must already hold the lock."""

        now = time.time()
        expired = [k for k, v in self._store.items() if v.expires_at < now]
        for k in expired:
            del self._store[k]


confirmations = ConfirmationStore()
