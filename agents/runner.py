"""Task execution and JSON-event polling for Codex commands running in tmux.

Turns are delivered via ``codex exec`` (first turn) or ``codex exec resume --last``
(subsequent turns), so each agent carries a real conversational session instead
of executing one-shot prompts. Output is parsed from ``--json`` events rather
than scraped from formatted text, which lets us capture both the final assistant
message and per-turn token usage.
"""

import asyncio
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from agents.manager import Agent, AgentStatus
from tmux.controller import tmux


TAIL_LINES = 50
DEFAULT_TIMEOUT = 600
POLL_INTERVAL = 2
# Assumed context window for the current Codex model. Used only to render a
# "% of context used" footer on successful replies. Adjust if you switch models.
CONTEXT_WINDOW_TOKENS = 400_000


@dataclass
class TurnResult:
    """Outcome of a single Codex turn: reply text, usage, and success flag."""

    text: str
    usage: dict | None
    ok: bool
    saw_turn_completed: bool


async def run_task(agent: Agent, prompt: str) -> str:
    """Run a single Codex prompt, resuming the agent's session when possible."""

    from agents.watchdog import watchdog

    agent.status = AgentStatus.RUNNING
    agent.current_task = prompt
    agent.last_task = prompt
    watchdog.touch(agent.name)

    # /reset sets reset_pending so the next turn starts a fresh Codex session
    # instead of resuming. We consume the flag here regardless of outcome.
    use_resume = not agent.reset_pending
    agent.reset_pending = False

    # Only turns after the first successful one should expect a prior session
    # to exist. On turn 1 of a fresh agent, resume is guaranteed to fail, so
    # we suppress the "Previous session unavailable" notice in that case.
    had_prior_session = agent.turn_count > 0

    result = await _execute_turn(agent, prompt, resume=use_resume)

    # Broad fallback: if resume was attempted but Codex never produced a
    # turn.completed event and exited non-zero, retry once as a fresh session.
    # This covers "no session found", bot-restart edge cases, and any other
    # resume failure we haven't enumerated yet.
    if use_resume and not result.saw_turn_completed and not result.ok:
        result = await _execute_turn(agent, prompt, resume=False)
        if had_prior_session:
            result.text = (
                "ℹ️ Previous session unavailable, starting fresh.\n\n"
                + result.text
            )

    if result.ok:
        agent.status = AgentStatus.IDLE
        agent.turn_count += 1
        if result.usage:
            agent.last_usage = result.usage
        footer = _format_usage_footer(result.usage, agent.turn_count)
        if footer:
            result.text = f"{result.text}\n\n{footer}"
    else:
        agent.status = AgentStatus.ERROR

    agent.current_task = None
    return result.text


async def _execute_turn(agent: Agent, prompt: str, resume: bool) -> TurnResult:
    """Fire one Codex invocation into tmux and wait for its turn to complete."""

    turn_id = uuid.uuid4().hex[:12]
    start_marker = f"---TURN-START-{turn_id}---"
    end_marker = f"---TURN-END-{turn_id}---"

    # Stage the prompt via a temp file and pipe it to `codex exec ... -`
    # (stdin mode). Avoids shell-escaping newlines, quotes, or unicode.
    prompt_file = Path(tempfile.gettempdir()) / f"codex-prompt-{turn_id}.txt"
    prompt_file.write_text(prompt)

    # Exec-level flags must come BEFORE the `resume` sub-subcommand — clap
    # parses parent-command options first, then dispatches to the subcommand.
    # Putting them after `resume` causes Codex to reject `--json` etc. with
    # "unexpected argument" and exit non-zero, which triggered the fallback
    # on every turn and silently broke conversational continuity.
    base_flags = (
        f"--full-auto --skip-git-repo-check --json -C '{agent.repo_path}'"
    )
    if resume:
        codex_cmd = (
            f"cat '{prompt_file}' | codex exec {base_flags} resume --last -"
        )
    else:
        codex_cmd = f"cat '{prompt_file}' | codex exec {base_flags} -"

    # Bracket the codex run with echoed markers so the polling loop can slice
    # exactly this turn's output out of the shared append-mode log.
    shell_cmd = (
        f"echo {start_marker} ; {codex_cmd} ; "
        f"RC=$? ; echo {end_marker}:$RC ; rm -f '{prompt_file}'"
    )

    start_offset = (
        agent.log_path.stat().st_size if agent.log_path.exists() else 0
    )

    await tmux.send_command(agent.name, shell_cmd)

    return await _wait_for_turn(agent, start_marker, end_marker, start_offset)


async def _wait_for_turn(
    agent: Agent,
    start_marker: str,
    end_marker: str,
    start_offset: int,
    timeout: int = DEFAULT_TIMEOUT,
) -> TurnResult:
    """Poll the tmux log until the end marker appears, parsing JSON events."""

    from agents.watchdog import watchdog

    elapsed = 0
    last_size = start_offset

    while elapsed < timeout:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        if not agent.log_path.exists():
            continue

        size = agent.log_path.stat().st_size
        if size > last_size:
            watchdog.touch(agent.name)
            last_size = size

        with agent.log_path.open("r", errors="replace") as f:
            f.seek(start_offset)
            chunk = f.read()

        # The shell echoes the full command line (including the marker literal)
        # before running it, so rfind picks the *stdout* echo of the marker
        # rather than the command-line copy.
        idx = chunk.rfind(start_marker)
        if idx < 0:
            continue
        after_start = chunk[idx + len(start_marker):]

        if end_marker not in after_start:
            continue

        body, _, tail = after_start.partition(end_marker)
        exit_code = _parse_exit_code(tail)
        text, usage, saw_completed = _parse_json_stream(body)

        ok = exit_code == 0 and saw_completed
        if not text:
            # No agent_message items parsed — surface raw tail as a fallback
            # so the user still sees something (usually an error message).
            text = body.strip()[-2000:] or "(no output)"

        return TurnResult(
            text=_trim_tail(text),
            usage=usage,
            ok=ok,
            saw_turn_completed=saw_completed,
        )

    return TurnResult(
        text=f"Task timed out after {timeout}s",
        usage=None,
        ok=False,
        saw_turn_completed=False,
    )


def _parse_exit_code(tail: str) -> int:
    """Read the ``:<code>`` suffix printed right after the end marker."""

    stripped = tail.lstrip()
    if not stripped.startswith(":"):
        return -1
    remainder = stripped[1:]
    token = remainder.split(None, 1)[0] if remainder.split() else ""
    try:
        return int(token)
    except ValueError:
        return -1


def _parse_json_stream(body: str) -> tuple[str, dict | None, bool]:
    """Collect agent_message text and usage from ``--json`` event lines."""

    messages: list[str] = []
    usage: dict | None = None
    saw_completed = False

    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")
        if etype == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text")
                if text:
                    messages.append(text)
        elif etype == "turn.completed":
            saw_completed = True
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = raw_usage

    return "\n\n".join(messages).strip(), usage, saw_completed


def _format_usage_footer(usage: dict | None, turn_count: int) -> str:
    """Render a compact ``📊 NN.Nk / 400k (X%) · turn N`` footer for replies.

    Uses ``input_tokens`` from Codex's ``turn.completed`` event as the running
    context size. ``CONTEXT_WINDOW_TOKENS`` is a static assumption — adjust it
    if you switch to a model with a different context window.
    """

    if not isinstance(usage, dict):
        return f"📊 turn {turn_count}"

    input_tokens = usage.get("input_tokens")
    if not isinstance(input_tokens, int):
        return f"📊 turn {turn_count}"

    used_k = input_tokens / 1000
    window_k = CONTEXT_WINDOW_TOKENS / 1000
    percent = (input_tokens / CONTEXT_WINDOW_TOKENS) * 100

    return (
        f"📊 {used_k:.1f}k / {window_k:.0f}k ({percent:.1f}%) · turn {turn_count}"
    )


def _trim_tail(text: str) -> str:
    """Clamp very long outputs so Telegram replies stay under size limits."""

    lines = text.strip().split("\n")
    if len(lines) > TAIL_LINES:
        lines = ["... (truncated) ..."] + lines[-TAIL_LINES:]
    return "\n".join(lines)


async def get_logs(agent: Agent, n_lines: int = 30) -> str:
    """Return the most recent log lines for an agent."""

    if not agent.log_path.exists():
        return "No logs available."

    content = agent.log_path.read_text(errors="replace")
    lines = content.strip().split("\n")
    if len(lines) > n_lines:
        lines = lines[-n_lines:]
    return "\n".join(lines)
