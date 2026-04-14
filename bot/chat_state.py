"""Per-chat active-agent state with JSON persistence."""

import json
from pathlib import Path
from threading import Lock


STATE_DIR = Path(__file__).parent.parent / "state"
STATE_FILE = STATE_DIR / "chat_state.json"


class ChatStateManager:
    """Track which agent is active for each Telegram chat."""

    def __init__(self, state_file: Path = STATE_FILE):
        self._state_file = state_file
        self._state: dict[str, dict] = {}
        self._lock = Lock()
        self._load()

    def _load(self):
        """Load persisted state from disk if it exists."""

        if not self._state_file.exists():
            return
        try:
            with self._state_file.open() as f:
                self._state = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable — start fresh rather than crashing on boot.
            self._state = {}

    def _save(self):
        """Persist the current state atomically."""

        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(self._state, f, indent=2)
        tmp.replace(self._state_file)

    def get_active(self, chat_id: int) -> str | None:
        """Return the active agent name for a chat, or None if unset."""

        with self._lock:
            entry = self._state.get(str(chat_id))
            return entry.get("active_agent") if entry else None

    def set_active(self, chat_id: int, agent_name: str):
        """Set the active agent for a chat and persist the change."""

        with self._lock:
            self._state[str(chat_id)] = {"active_agent": agent_name}
            self._save()

    def clear_active(self, chat_id: int):
        """Remove any active-agent record for a chat."""

        with self._lock:
            self._state.pop(str(chat_id), None)
            self._save()

    def clear_agent_everywhere(self, agent_name: str):
        """Drop a deleted agent from every chat that had it active."""

        with self._lock:
            changed = False
            for chat_id in list(self._state.keys()):
                if self._state[chat_id].get("active_agent") == agent_name:
                    del self._state[chat_id]
                    changed = True
            if changed:
                self._save()


chat_state = ChatStateManager()
