"""Per-agent FIFO queue used when tasks arrive during an active run."""

import asyncio
import logging
from dataclasses import dataclass

from agents.manager import Agent, AgentStatus

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Queued unit of work destined for a specific agent."""

    prompt: str
    agent_name: str


class TaskQueue:
    """Own the pending work list and consumer loop for one agent."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None
        self._notify_callback = None

    def set_notify_callback(self, callback):
        """Register a coroutine used to publish queue progress to Telegram."""

        self._notify_callback = callback

    async def enqueue(self, prompt: str) -> int:
        """Append a task and return its 1-based queue position."""

        task = Task(prompt=prompt, agent_name=self.agent.name)
        await self._queue.put(task)
        position = self._queue.qsize()
        logger.info(f"[{self.agent.name}] Queued task at position {position}: {prompt[:50]}")
        self._ensure_consumer_running()
        return position

    def _ensure_consumer_running(self):
        """Start the background consumer once per queue lifecycle."""

        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume())

    async def _consume(self):
        """Drain queued tasks whenever the agent becomes available."""

        from agents.runner import run_task

        while not self._queue.empty():
            task = await self._queue.get()

            if self.agent.status == AgentStatus.RUNNING:
                # Preserve FIFO order while waiting for the active task to finish.
                await self._queue.put(task)
                await asyncio.sleep(5)
                continue

            logger.info(f"[{self.agent.name}] Dequeuing task: {task.prompt[:50]}")

            if self._notify_callback:
                await self._notify_callback(
                    f"Starting queued task on '{self.agent.name}': {task.prompt}"
                )

            output = await run_task(self.agent, task.prompt)

            if self._notify_callback:
                await self._notify_callback(
                    f"Queued task finished on '{self.agent.name}':\n```\n{output[:3500]}\n```"
                )

            self._queue.task_done()

    def pending_tasks(self) -> list[str]:
        """Expose queued prompts for status display."""

        # asyncio.Queue does not offer a public snapshot API.
        return [t.prompt for t in list(self._queue._queue)]

    def clear(self) -> int:
        """Drop all pending tasks and return how many were removed."""

        count = self._queue.qsize()
        self._queue._queue.clear()
        return count

    @property
    def size(self) -> int:
        """Return the current queue length."""

        return self._queue.qsize()


class QueueManager:
    """Shared registry of task queues keyed by agent name."""

    def __init__(self):
        self._queues: dict[str, TaskQueue] = {}

    def get_or_create(self, agent: Agent) -> TaskQueue:
        """Return the queue for an agent, creating it on first use."""

        if agent.name not in self._queues:
            self._queues[agent.name] = TaskQueue(agent)
        return self._queues[agent.name]

    def remove(self, agent_name: str):
        """Forget the queue for a deleted agent."""

        self._queues.pop(agent_name, None)


queue_manager = QueueManager()
