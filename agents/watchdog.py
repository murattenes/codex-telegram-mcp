"""Background watchdog that recovers hung or stale tmux-backed agents."""

import asyncio
import logging
import time

from agents.manager import Agent, AgentStatus, agent_manager
from tmux.controller import tmux

logger = logging.getLogger(__name__)

IDLE_TIMEOUT = 180       # seconds with no output → consider hung
SESSION_TTL = 1800       # 30 min idle → kill session
CLEANUP_INTERVAL = 60    # check every 60s


class Watchdog:
    """Track agent activity and refresh broken or idle tmux sessions."""

    def __init__(self):
        self._last_output_times: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._notify_callback = None

    def set_notify_callback(self, callback):
        """Register a coroutine for watchdog-originated user notifications."""

        self._notify_callback = callback

    def start(self):
        """Start the background watchdog loop once."""

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info("Watchdog started")

    def stop(self):
        """Stop the background watchdog loop."""

        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Watchdog stopped")

    def touch(self, agent_name: str):
        """Record that an agent produced output just now."""

        self._last_output_times[agent_name] = time.time()

    def remove(self, agent_name: str):
        """Forget watchdog state for a deleted agent."""

        self._last_output_times.pop(agent_name, None)

    async def _run(self):
        """Periodically inspect agents for hangs, crashes, and stale sessions."""

        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                await self._check_agents()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")

    async def _check_agents(self):
        """Apply timeout and health checks to every registered agent."""

        now = time.time()

        for agent in agent_manager.list_agents():
            last_output = self._last_output_times.get(agent.name, now)

            if agent.status == AgentStatus.RUNNING:
                idle_duration = now - last_output
                if idle_duration > IDLE_TIMEOUT:
                    logger.warning(
                        f"[{agent.name}] No output for {idle_duration:.0f}s, killing..."
                    )
                    await self._kill_hung_agent(agent)
                    continue

            if agent.status == AgentStatus.IDLE:
                idle_duration = now - last_output
                if idle_duration > SESSION_TTL:
                    logger.info(f"[{agent.name}] Idle for {idle_duration:.0f}s, cleaning up...")
                    await self._cleanup_idle_agent(agent)

            # Detect broken tmux sessions so the bot does not report a false healthy state.
            if not await tmux.has_session(agent.name):
                if agent.status == AgentStatus.RUNNING:
                    logger.warning(f"[{agent.name}] tmux session crashed")
                    agent.status = AgentStatus.ERROR
                    agent.current_task = None
                    if self._notify_callback:
                        await self._notify_callback(
                            f"Agent '{agent.name}' tmux session crashed."
                        )

    async def _kill_hung_agent(self, agent: Agent):
        """Kill and recreate a stuck tmux session while preserving the agent record."""

        await tmux.kill_session(agent.name)
        agent.status = AgentStatus.ERROR
        task = agent.current_task
        agent.current_task = None

        if self._notify_callback:
            await self._notify_callback(
                f"Agent '{agent.name}' was hung (no output for {IDLE_TIMEOUT}s). "
                f"Session killed. Task was: {task}\n"
                f"Use /retry {agent.name} to retry."
            )

        # Recreate the session immediately so the agent can accept the next command.
        await tmux.create_session(agent.name, agent.repo_path)
        await tmux.setup_pipe_pane(agent.name, agent.log_path)

    async def _cleanup_idle_agent(self, agent: Agent):
        """Refresh long-idle sessions without deleting the agent itself."""

        await tmux.kill_session(agent.name)
        await tmux.create_session(agent.name, agent.repo_path)
        await tmux.setup_pipe_pane(agent.name, agent.log_path)
        self.touch(agent.name)
        logger.info(f"[{agent.name}] Session refreshed after idle timeout")


watchdog = Watchdog()
