# Telegram Codex Agent - Implementation Plan

## Context
Build a Telegram bot that controls OpenAI's Codex CLI remotely via tmux sessions. The bot supports multiple agents, per-agent task queues, git operations, file upload, and voice transcription. Python 3.11, macOS, local only.

## Tech Stack
- `python-telegram-bot` (async, long polling)
- OpenAI Codex CLI in full-auto mode
- tmux for persistent sessions
- Whisper for voice transcription
- Whitelist-based auth (single user, expandable)

## Architecture
```
Telegram
   ↓
Telegram Bot (long polling)
   ↓
Command Router
   ↓
Agent Manager
   ├── Queue Manager
   ├── Git Manager
   ├── Voice Handler
   ├── File Handler
   └── Retry Manager
         ↓
tmux sessions (one per agent)
         ↓
Codex CLI (full-auto)
```

## Repo Structure
```
codex-telegram-mcp/
├── main.py                  # Entry point
├── .env                     # Secrets (BOT_TOKEN, ALLOWED_USERS, GITHUB_TOKEN)
├── .env.example             # Template
├── requirements.txt
├── config.py                # Pydantic settings loader
├── bot/
│   ├── __init__.py
│   ├── app.py               # Bot setup, handler registration
│   └── handlers.py          # All command handlers
├── agents/
│   ├── __init__.py
│   ├── manager.py           # AgentManager (create/delete/list agents)
│   ├── queue.py             # Per-agent task queue
│   ├── runner.py            # Run codex in tmux, capture output
│   ├── retry.py             # Auto-retry with backoff
│   └── watchdog.py          # Idle timeout monitor, session cleanup
├── tmux/
│   ├── __init__.py
│   └── controller.py        # tmux session CRUD, pipe-pane logging
├── git/
│   ├── __init__.py
│   ├── operations.py        # commit, push, diff, branch
│   └── pr.py                # PR creation via gh CLI
├── repo/
│   ├── __init__.py
│   └── manager.py           # Multi-repo registry, agent-repo binding
├── voice/
│   ├── __init__.py
│   └── transcribe.py        # Whisper transcription
├── files/
│   ├── __init__.py
│   └── uploader.py          # File download from Telegram, stage for agent
└── logs/                    # Runtime log files (gitignored)
```

## Commands
```
/agent create <name>        — Create a new agent
/agent list                 — List all agents
/agent delete <name>        — Delete an agent
/run <agent> <task>         — Run a task on an agent
/continue <agent>           — Re-run last task
/retry <agent>              — Retry last failed task
/queue <agent>              — Show agent's task queue
/queue clear <agent>        — Clear agent's queue
/diff <agent>               — Preview git diff
/commit <agent> "msg"       — Commit changes
/push <agent>               — Push current branch
/pr <agent> "title"         — Create PR
/repo add <name> <path>     — Register a repo
/repo list                  — List repos
/repo switch <agent> <repo> — Bind agent to repo
/logs <agent>               — Show agent logs
/status                     — Show all agent statuses
```

---

## Phase 1: Foundation + Single Agent `/run`
**Goal:** Bot starts, authenticates user, creates one agent, runs a task via Codex CLI in tmux, returns output.

Files: `.env.example`, `requirements.txt`, `config.py`, `tmux/controller.py`, `agents/manager.py`, `agents/runner.py`, `bot/app.py`, `bot/handlers.py`, `main.py`

Key behaviors:
- Auth via Telegram user ID whitelist
- tmux session per agent with pipe-pane logging
- Codex CLI runs in full-auto mode
- Output streamed via log file tailing

---

## Phase 2: Queue + Retry + Continue + Watchdog
**Goal:** Tasks queue up per agent. Failed tasks auto-retry. Agents can continue last task. Hung agents get killed.

Files: `agents/queue.py`, `agents/retry.py`, `agents/watchdog.py`

Key behaviors:
- FIFO queue per agent with asyncio.Queue
- Auto-retry up to 3x with exponential backoff
- Idle timeout watchdog (180s no output -> kill + retry)
- Session cleanup loop (60s interval, 30min idle TTL)

---

## Phase 3: Git Operations
**Goal:** Diff preview, commit, push, branch creation, PR generation from Telegram.

Files: `git/operations.py`, `git/pr.py`

Key behaviors:
- All git ops run in agent's bound repo directory
- Diff output split for Telegram's 4096 char limit
- PR creation via `gh pr create`

---

## Phase 4: Multi-Repo Support
**Goal:** Register multiple repos, bind agents to repos, switch repos.

Files: `repo/manager.py`

Key behaviors:
- Repo registry persisted to `repos.json`
- Agent-repo binding with live switching

---

## Phase 5: File Upload + Voice Input
**Goal:** Upload files to agent's repo, send voice messages as tasks.

Files: `files/uploader.py`, `voice/transcribe.py`

Key behaviors:
- Files downloaded from Telegram saved to agent's repo
- Voice messages transcribed with Whisper then executed as tasks
