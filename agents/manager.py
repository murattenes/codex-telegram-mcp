"""Agent registry, on-disk persistence, and tmux reconciliation.

Agents are persisted to ``state/agents.json`` so they survive bot restarts.
On startup, :meth:`AgentManager.reconcile_with_tmux` walks the persisted set
and makes sure every agent has a live ``codex-<name>`` tmux session — missing
ones are recreated at the stored repo path. If a tmux session already exists
when :meth:`create_agent` is called (for example after the user cleared their
Telegram chat history), the runner adopts the existing session instead of
failing with "session already exists".
"""

import json
import logging
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field

from tmux.controller import tmux
from config import settings

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    """High-level runtime state reported to Telegram users."""

    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class Agent:
    """In-memory record describing one named Codex worker."""

    name: str
    repo_path: Path
    status: AgentStatus = AgentStatus.IDLE
    current_task: str | None = None
    last_task: str | None = None
    # Set by /reset; consumed on the next run_task() call to skip `resume --last`
    # and start a fresh Codex session instead.
    reset_pending: bool = False
    # Latest `turn.completed.usage` payload from Codex --json events. Used by
    # /status to surface context size (input_tokens) as a token indicator.
    last_usage: dict | None = None
    # Count of successfully completed turns on this agent's current session.
    turn_count: int = 0

    @property
    def log_path(self) -> Path:
        """Return the per-agent log file used by tmux pipe-pane."""

        return settings.log_dir / f"{self.name}.log"

    def to_dict(self) -> dict:
        """Serialize the persistable fields to a JSON-safe dict."""

        return {
            "repo_path": str(self.repo_path),
            "turn_count": self.turn_count,
            "last_usage": self.last_usage,
            "last_task": self.last_task,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Agent":
        """Rebuild an Agent from a persisted dict. Ignores unknown keys."""

        return cls(
            name=name,
            repo_path=Path(data["repo_path"]),
            turn_count=int(data.get("turn_count") or 0),
            last_usage=data.get("last_usage"),
            last_task=data.get("last_task"),
        )


class AgentManager:
    """Create, look up, and delete agents for the current process."""

    def __init__(self):
        self._agents: dict[str, Agent] = {}
        self._state_path: Path = settings.state_dir / "agents.json"
        self._load()

    # ---- persistence ----

    def _load(self) -> None:
        """Populate ``_agents`` from the on-disk JSON file if it exists."""

        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to read %s (%s); starting with empty agent list",
                self._state_path, exc,
            )
            return

        for name, data in raw.items():
            try:
                self._agents[name] = Agent.from_dict(name, data)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Skipping malformed agent '%s': %s", name, exc)

    def save(self) -> None:
        """Write the current agent registry to disk atomically."""

        payload = {name: agent.to_dict() for name, agent in self._agents.items()}
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._state_path)

    # ---- tmux reconciliation ----

    async def reconcile_with_tmux(self) -> None:
        """Ensure every persisted agent has a live tmux session.

        Called once at startup. For each persisted agent, verify the tmux
        session exists; recreate it at the stored repo path if it's missing.
        Re-establish ``pipe-pane`` unconditionally so the log mirror survives
        a bot restart even though tmux itself survived.
        """

        for agent in list(self._agents.values()):
            if not await tmux.has_session(agent.name):
                if not agent.repo_path.is_dir():
                    logger.error(
                        "Cannot restore agent '%s': repo path %s missing",
                        agent.name, agent.repo_path,
                    )
                    continue
                logger.info(
                    "Restoring missing tmux session for agent '%s' at %s",
                    agent.name, agent.repo_path,
                )
                await tmux.create_session(agent.name, agent.repo_path)
            await tmux.setup_pipe_pane(agent.name, agent.log_path)

        # Warn (but don't touch) stale codex-* tmux sessions with no JSON entry.
        live = set(await tmux.list_sessions())
        stale = live - set(self._agents.keys())
        if stale:
            logger.warning(
                "Found %d unknown codex-* tmux session(s) with no persisted "
                "agent record: %s. Use /new <name> to adopt one, or kill "
                "them manually with `tmux kill-session -t codex-<name>`.",
                len(stale), ", ".join(sorted(stale)),
            )

    # ---- CRUD ----

    async def create_agent(
        self, name: str, repo_path: Path | None = None
    ) -> Agent:
        """Create an agent, adopting any pre-existing tmux session of the same name."""

        if name in self._agents:
            raise ValueError(f"Agent '{name}' already exists")

        repo = repo_path or settings.default_repo_path
        if not repo.is_dir():
            raise ValueError(f"Repo path does not exist: {repo}")

        agent = Agent(name=name, repo_path=repo)

        if await tmux.has_session(name):
            # A prior bot run left this session behind (or the user cleared
            # their Telegram state). Adopt it instead of erroring.
            logger.info(
                "Adopting existing tmux session for '%s'; the user's bot "
                "state and the tmux world had drifted.",
                name,
            )
        else:
            created = await tmux.create_session(name, repo)
            if not created:
                raise RuntimeError(
                    f"Failed to create tmux session for '{name}'"
                )

        # Mirror everything printed in the tmux pane into the agent log file.
        await tmux.setup_pipe_pane(name, agent.log_path)
        self._agents[name] = agent
        self.save()
        return agent

    async def delete_agent(self, name: str) -> bool:
        """Tear down an agent and remove its related runtime state."""

        if name not in self._agents:
            raise ValueError(f"Agent '{name}' does not exist")

        await tmux.stop_pipe_pane(name)
        await tmux.kill_session(name)
        del self._agents[name]
        self.save()

        # Clean up queue and watchdog references
        from agents.queue import queue_manager
        from agents.watchdog import watchdog
        queue_manager.remove(name)
        watchdog.remove(name)

        return True

    def get_agent(self, name: str) -> Agent:
        """Return a single agent or raise if it does not exist."""

        if name not in self._agents:
            raise ValueError(f"Agent '{name}' does not exist")
        return self._agents[name]

    def list_agents(self) -> list[Agent]:
        """Return all registered agents."""

        return list(self._agents.values())

    def has_agent(self, name: str) -> bool:
        """Check whether an agent name is already registered."""

        return name in self._agents


agent_manager = AgentManager()
