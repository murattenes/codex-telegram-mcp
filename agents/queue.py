import asyncio
import logging
from dataclasses import dataclass, field

from agents.manager import Agent, AgentStatus

logger = logging.getLogger(__name__)


@dataclass
class Task:
    prompt: str
    agent_name: str


class TaskQueue:
    def __init__(self, agent: Agent):
        self.agent = agent
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None
        self._notify_callback = None

    def set_notify_callback(self, callback):
        self._notify_callback = callback

    async def enqueue(self, prompt: str) -> int:
        task = Task(prompt=prompt, agent_name=self.agent.name)
        await self._queue.put(task)
        position = self._queue.qsize()
        logger.info(f"[{self.agent.name}] Queued task at position {position}: {prompt[:50]}")
        self._ensure_consumer_running()
        return position

    def _ensure_consumer_running(self):
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume())

    async def _consume(self):
        from agents.runner import run_task

        while not self._queue.empty():
            task = await self._queue.get()

            if self.agent.status == AgentStatus.RUNNING:
                # Put it back and wait
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
        # Access internal queue list for display
        return [t.prompt for t in list(self._queue._queue)]

    def clear(self) -> int:
        count = self._queue.qsize()
        self._queue._queue.clear()
        return count

    @property
    def size(self) -> int:
        return self._queue.qsize()


class QueueManager:
    def __init__(self):
        self._queues: dict[str, TaskQueue] = {}

    def get_or_create(self, agent: Agent) -> TaskQueue:
        if agent.name not in self._queues:
            self._queues[agent.name] = TaskQueue(agent)
        return self._queues[agent.name]

    def remove(self, agent_name: str):
        self._queues.pop(agent_name, None)


queue_manager = QueueManager()
