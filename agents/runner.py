import asyncio
import re
from pathlib import Path

from agents.manager import Agent, AgentStatus, agent_manager
from tmux.controller import tmux
from config import settings


COMPLETION_MARKER = "---CODEX-TASK-DONE---"
ERROR_MARKER = "---CODEX-TASK-ERROR---"
TAIL_LINES = 50


async def run_task(agent: Agent, prompt: str) -> str:
    from agents.watchdog import watchdog

    agent.status = AgentStatus.RUNNING
    agent.current_task = prompt
    agent.last_task = prompt
    watchdog.touch(agent.name)

    # Clear the log file
    agent.log_path.write_text("")

    # Build the codex command — capture exit code
    escaped_prompt = prompt.replace("'", "'\\''")
    command = (
        f"codex --full-auto '{escaped_prompt}' ; "
        f"EXIT_CODE=$? ; "
        f"if [ $EXIT_CODE -eq 0 ]; then echo '{COMPLETION_MARKER}'; "
        f"else echo '{ERROR_MARKER}'; fi"
    )

    await tmux.send_command(agent.name, command)

    # Wait for completion by tailing the log
    output = await _wait_for_completion(agent)

    if agent.status != AgentStatus.ERROR:
        agent.status = AgentStatus.IDLE
    agent.current_task = None

    return output


async def _wait_for_completion(agent: Agent, timeout: int = 600) -> str:
    from agents.watchdog import watchdog

    elapsed = 0
    poll_interval = 2
    last_size = 0

    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        if not agent.log_path.exists():
            continue

        content = agent.log_path.read_text(errors="replace")

        # Update watchdog if new output appeared
        if len(content) > last_size:
            watchdog.touch(agent.name)
            last_size = len(content)

        if COMPLETION_MARKER in content:
            output = content.split(COMPLETION_MARKER)[0].strip()
            return _clean_output(output)

        if ERROR_MARKER in content:
            agent.status = AgentStatus.ERROR
            output = content.split(ERROR_MARKER)[0].strip()
            return _clean_output(output)

    agent.status = AgentStatus.ERROR
    return f"Task timed out after {timeout}s"


def _clean_output(raw: str) -> str:
    # Remove ANSI escape codes
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    cleaned = ansi_escape.sub('', raw)

    # Trim to last N lines if too long
    lines = cleaned.strip().split('\n')
    if len(lines) > TAIL_LINES:
        lines = ["... (truncated) ..."] + lines[-TAIL_LINES:]

    return '\n'.join(lines)


async def get_logs(agent: Agent, n_lines: int = 30) -> str:
    if not agent.log_path.exists():
        return "No logs available."

    content = agent.log_path.read_text(errors="replace")
    lines = content.strip().split('\n')

    if len(lines) > n_lines:
        lines = lines[-n_lines:]

    return _clean_output('\n'.join(lines))
