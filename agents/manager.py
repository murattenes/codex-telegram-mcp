from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

from tmux.controller import tmux
from config import settings


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class Agent:
    name: str
    repo_path: Path
    status: AgentStatus = AgentStatus.IDLE
    current_task: str | None = None
    last_task: str | None = None

    @property
    def log_path(self) -> Path:
        return settings.log_dir / f"{self.name}.log"


class AgentManager:
    def __init__(self):
        self._agents: dict[str, Agent] = {}

    async def create_agent(self, name: str, repo_path: Path | None = None) -> Agent:
        if name in self._agents:
            raise ValueError(f"Agent '{name}' already exists")

        repo = repo_path or settings.default_repo_path
        if not repo.is_dir():
            raise ValueError(f"Repo path does not exist: {repo}")

        agent = Agent(name=name, repo_path=repo)

        created = await tmux.create_session(name, repo)
        if not created:
            raise RuntimeError(f"Failed to create tmux session for '{name}'")

        await tmux.setup_pipe_pane(name, agent.log_path)
        self._agents[name] = agent
        return agent

    async def delete_agent(self, name: str) -> bool:
        if name not in self._agents:
            raise ValueError(f"Agent '{name}' does not exist")

        await tmux.stop_pipe_pane(name)
        await tmux.kill_session(name)
        del self._agents[name]
        return True

    def get_agent(self, name: str) -> Agent:
        if name not in self._agents:
            raise ValueError(f"Agent '{name}' does not exist")
        return self._agents[name]

    def list_agents(self) -> list[Agent]:
        return list(self._agents.values())

    def has_agent(self, name: str) -> bool:
        return name in self._agents


agent_manager = AgentManager()
