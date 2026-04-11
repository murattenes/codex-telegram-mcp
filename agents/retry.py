"""Retry helpers for rerunning failed Codex tasks with backoff."""

import asyncio
import logging

from agents.manager import Agent, AgentStatus

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY = 5  # seconds


async def run_with_retry(agent: Agent, prompt: str, notify_callback=None) -> str:
    """Retry a task until it succeeds or the retry budget is exhausted."""

    from agents.runner import run_task

    last_output = ""

    for attempt in range(1, MAX_RETRIES + 1):
        output = await run_task(agent, prompt)
        last_output = output

        if agent.status != AgentStatus.ERROR:
            return output

        if attempt < MAX_RETRIES:
            # Backoff reduces repeated failures from transient Codex or environment issues.
            delay = BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"[{agent.name}] Task failed (attempt {attempt}/{MAX_RETRIES}), "
                f"retrying in {delay}s..."
            )
            if notify_callback:
                await notify_callback(
                    f"Task failed on '{agent.name}' (attempt {attempt}/{MAX_RETRIES}). "
                    f"Retrying in {delay}s..."
                )
            await asyncio.sleep(delay)
        else:
            logger.error(f"[{agent.name}] Task failed after {MAX_RETRIES} attempts")
            if notify_callback:
                await notify_callback(
                    f"Task failed on '{agent.name}' after {MAX_RETRIES} attempts."
                )

    return last_output
