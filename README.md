# Codex Telegram MCP

Control OpenAI's Codex CLI remotely from Telegram with a chat-first, phone-friendly interface. Each agent runs inside its own persistent `tmux` session tied to a repository, and you interact with it by typing naturally — no slash command required for every step.

This project is a **local-first** automation layer: it runs on your machine, talks to your local Codex CLI, and exposes it through a Telegram bot so you can drive coding work from your phone.

## Core Idea

In any Telegram chat, one agent is the **active agent**. From that point on:

- **Plain text** is forwarded to that agent's Codex session.
- **Slash commands** are for session and bot control.
- **Four exact phrases** are handled directly by the bot for high-value git actions.

That's the whole mental model.

```text
/new backend ~/code/my-app        ← create agent
/use backend                      ← select active agent for this chat
the login button is broken on mobile, find and fix it
                                  ← forwarded to Codex
show me the diff                  ← bot runs git diff, posts formatted output
commit it: fix mobile login       ← bot commits
push it                           ← [Confirm push] [Cancel]
open a pr: fix mobile login       ← [Confirm PR] [Cancel]
```

## Features

- Multiple named Codex agents, each bound to a repository
- One persistent `tmux` session per agent with log capture via `pipe-pane`
- **Chat-first UX**: plain text goes to Codex, no `/run` needed
- **Four exact-match fast-paths** for diff, commit, push, and PR — no regex intent engine
- **Command backups** for the same git actions (`/diff`, `/commit`, `/push`, `/pr`)
- **Per-chat active agent**, persisted across bot restarts
- **Inline confirmation buttons** for destructive actions (push, PR)
- Per-agent FIFO task queue with automatic enqueuing while busy
- Auto-retry with exponential backoff on failed tasks
- Watchdog that kills hung tmux sessions after an idle timeout
- Whitelist-based Telegram access control
- Long-polling, so no webhook setup needed

## Architecture

```text
Telegram
   ↓
Telegram Bot (long polling)
   ↓
Auth middleware
   ↓
┌──────────────┬──────────────────────┐
│ Slash command│ Plain text / callback│
│              │                      │
│ Handlers     │ MessageRouter        │
│              │   ├── Classifier     │
│              │   │   (4 exact rules)│
│              │   ├── git_actions    │
│              │   └── passthrough    │
└──────┬───────┴──────────┬───────────┘
       ↓                  ↓
AgentManager  ←→  Queue / Retry / Watchdog
       ↓
  tmux session
       ↓
   Codex CLI
```

## Fast-Paths

The bot intercepts exactly these four phrases (case-insensitive, trimmed):

| Input | Action |
|---|---|
| `show me the diff` | Run `git diff`, post formatted chunks |
| `commit it: <message>` | Stage all, commit with `<message>` |
| `push it` | Show `[Confirm push] [Cancel]` → on confirm, push |
| `open a pr: <title>` | Show `[Confirm PR] [Cancel]` → on confirm, commit + push + `gh pr create` |

Everything else is forwarded to Codex unchanged. If you want the bot to run one of these actions but can't remember the exact phrase, use the backup commands instead: `/diff`, `/commit <msg>`, `/push`, `/pr <title>`.

## Commands

### Session Control
```text
/new <name> [repo_path]  Create a new agent (repo defaults to DEFAULT_REPO_PATH)
/use <name>              Set the active agent for this chat
/agents                  List agents with inline select buttons
/delete <name>           Delete an agent
/status                  Show all agents and which is active
/logs                    Tail the active agent's log file
/stop                    Cancel the currently running task
/help                    Show the full command reference
```

### Git Backup Commands
```text
/diff                    Same as "show me the diff"
/commit <message>        Same as "commit it: <message>"
/push                    Same as "push it"
/pr <title>              Same as "open a pr: <title>"
```

## Project Structure

```text
codex-telegram-mcp/
├── main.py                # Entry point
├── config.py              # Pydantic settings loader
├── requirements.txt
├── .env.example
├── IMPLEMENTATION_PLAN.md # Source-of-truth design doc (v2)
├── PROGRESS.md            # Cross-session handoff notes
├── bot/
│   ├── app.py             # Bot wiring and handler registration
│   ├── handlers.py        # Session / control / git backup commands
│   ├── router.py          # MessageRouter for plain text + callbacks
│   ├── classifier.py      # 4 exact-match fast-path rules
│   ├── git_actions.py     # Telegram-formatted git wrappers
│   ├── chat_state.py      # Per-chat active-agent state (persisted)
│   └── confirmations.py   # TTL store for inline confirm buttons
├── agents/
│   ├── manager.py         # Agent dataclass + AgentManager
│   ├── runner.py          # Runs codex --full-auto in tmux
│   ├── queue.py           # Per-agent FIFO task queue
│   ├── retry.py           # Auto-retry with exponential backoff
│   └── watchdog.py        # Idle-timeout + session cleanup
├── tmux/
│   └── controller.py      # Async tmux session CRUD + pipe-pane
├── git/
│   ├── operations.py      # diff, commit, push, branch, status
│   └── pr.py              # Branch + commit + push + gh pr create
├── state/                 # Persisted chat state (gitignored)
└── logs/                  # tmux pipe-pane log files (gitignored)
```

## Requirements

- Python 3.11+
- macOS
- `tmux`
- OpenAI Codex CLI on `$PATH` as `codex`
- `gh` CLI authenticated (`gh auth login`) for PR creation
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID from [@userinfobot](https://t.me/userinfobot)

## Installation

Using [`uv`](https://github.com/astral-sh/uv) (recommended):

```bash
uv venv .venv
uv pip install -r requirements.txt
```

Or with stock Python:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
ALLOWED_USER_IDS=[123456789]
DEFAULT_REPO_PATH=/absolute/path/to/default/repo
```

`ALLOWED_USER_IDS` must be a JSON-style list, even for a single user.

## Running

```bash
.venv/bin/python main.py
```

The bot runs with long polling, so there's no webhook to configure. It will log handler registration and watchdog startup to stdout.

## Typical Session

1. `/new backend ~/code/my-app` — creates an agent, offers a `[Use this agent]` button
2. Tap the button, or run `/use backend`
3. Type: `the login button is broken on mobile, find and fix it`
   — forwarded to Codex, result posted when done
4. Type: `show me the diff` — bot posts the diff
5. Type: `commit it: fix mobile login button` — bot commits
6. Type: `push it` — tap `[Confirm push]` → bot pushes
7. Type: `open a pr: fix mobile login` — tap `[Confirm PR]` → bot creates the PR
8. `/status` to see what every agent is doing

## Behavior Notes

- **No active agent + plain text**: if you have zero agents, the bot tells you to create one. If you have one agent, it auto-selects it. If you have two or more, it tells you to pick one and **does not replay** the message afterwards.
- **Agent busy + plain text**: the task is queued and you're told the queue position.
- **Agent busy + fast-path**: `show me the diff` runs anyway (read-only); commit, push, and PR are rejected until the task finishes.
- **Confirmation expiry**: push and PR confirmations expire after 2 minutes.
- **Hung tasks**: the watchdog kills any task that produces no output for 180 seconds and notifies you.

## Implementation Status

- Foundation, tmux, agent manager, runner ✅
- Queue, retry, watchdog ✅
- Git operations and PR creation ✅
- Chat-first router, fast-paths, confirmations, persistent state ✅
- Voice input (Whisper) — not yet
- File upload — not yet

See `IMPLEMENTATION_PLAN.md` for the full design and `PROGRESS.md` for cross-session context.

## Use Cases

- Run Codex remotely from your phone while away from your machine
- Keep separate agents for backend, frontend, infra, or experiments
- Ship fixes without opening a terminal — describe it, review the diff, commit, push, open a PR
- Queue up a few tasks on an agent and let Codex chew through them

## License

This project is licensed under the terms in [LICENSE](LICENSE).

Development is still in progress and some implementation details may continue to evolve.
