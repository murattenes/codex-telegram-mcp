import asyncio
import subprocess
from pathlib import Path


class TmuxController:
    SESSION_PREFIX = "codex-"

    def _session_name(self, agent_name: str) -> str:
        return f"{self.SESSION_PREFIX}{agent_name}"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
        )

    async def _async_run(self, *args: str) -> subprocess.CompletedProcess:
        return await asyncio.to_thread(self._run, *args)

    async def create_session(self, agent_name: str, working_dir: Path) -> bool:
        name = self._session_name(agent_name)
        if await self.has_session(agent_name):
            return False
        result = await self._async_run(
            "new-session", "-d", "-s", name, "-c", str(working_dir)
        )
        return result.returncode == 0

    async def kill_session(self, agent_name: str) -> bool:
        name = self._session_name(agent_name)
        result = await self._async_run("kill-session", "-t", name)
        return result.returncode == 0

    async def has_session(self, agent_name: str) -> bool:
        name = self._session_name(agent_name)
        result = await self._async_run("has-session", "-t", name)
        return result.returncode == 0

    async def list_sessions(self) -> list[str]:
        result = await self._async_run(
            "list-sessions", "-F", "#{session_name}"
        )
        if result.returncode != 0:
            return []
        return [
            s.removeprefix(self.SESSION_PREFIX)
            for s in result.stdout.strip().split("\n")
            if s.startswith(self.SESSION_PREFIX)
        ]

    async def send_command(self, agent_name: str, command: str) -> bool:
        name = self._session_name(agent_name)
        result = await self._async_run(
            "send-keys", "-t", name, command, "Enter"
        )
        return result.returncode == 0

    async def setup_pipe_pane(self, agent_name: str, log_path: Path) -> bool:
        name = self._session_name(agent_name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        result = await self._async_run(
            "pipe-pane", "-o", "-t", name, f"cat >> {log_path}"
        )
        return result.returncode == 0

    async def stop_pipe_pane(self, agent_name: str) -> bool:
        name = self._session_name(agent_name)
        result = await self._async_run("pipe-pane", "-t", name)
        return result.returncode == 0


tmux = TmuxController()
