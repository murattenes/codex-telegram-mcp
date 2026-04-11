"""Agent registry and lifecycle helpers for tmux-backed Codex workers."""

from enum import Enum
from pathlib import Path
from dataclasses import dataclass

from tmux.controller import tmux
from config import settings


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

    @property
    def log_path(self) -> Path:
        """Return the per-agent log file used by tmux pipe-pane."""

        return settings.log_dir / f"{self.name}.log"


class AgentManager:
    """Create, look up, and delete agents for the current process."""

    def __init__(self):
        self._agents: dict[str, Agent] = {}

    async def create_agent(self, name: str, repo_path: Path | None = None) -> Agent:
        """Create an agent and its backing tmux session."""

        if name in self._agents:
            raise ValueError(f"Agent '{name}' already exists")

        repo = repo_path or settings.default_repo_path
        if not repo.is_dir():
            raise ValueError(f"Repo path does not exist: {repo}")

        agent = Agent(name=name, repo_path=repo)

        created = await tmux.create_session(name, repo)
        if not created:
            raise RuntimeError(f"Failed to create tmux session for '{name}'")

        # Mirror everything printed in the tmux pane into the agent log file.
        await tmux.setup_pipe_pane(name, agent.log_path)
        self._agents[name] = agent
        return agent

    async def delete_agent(self, name: str) -> bool:
        """Tear down an agent and remove its related runtime state."""

        if name not in self._agents:
            raise ValueError(f"Agent '{name}' does not exist")

        await tmux.stop_pipe_pane(name)
        await tmux.kill_session(name)
        del self._agents[name]

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
