# Codex Telegram MCP

Control OpenAI Codex from Telegram with persistent multi-agent sessions, repo-aware workflows, git operations, file uploads, and voice-driven task execution.

`codex-telegram-mcp` is a local-first automation layer for running Codex remotely on your machine through a Telegram bot. Each agent runs inside its own `tmux` session, stays attached to a repository context, and can be used to execute coding tasks, inspect logs, manage git workflows, and coordinate work across multiple repos.

## Features

- Multiple named Codex agents
- Persistent `tmux` session per agent
- Per-agent task execution and queueing
- Retry and continue flows for interrupted or failed tasks
- Multi-repo registry and agent-to-repo switching
- Git diff, commit, push, branch, and PR workflows
- File upload into the active repo
- Voice message transcription into runnable tasks
- Whitelist-based Telegram access control
- Local execution on macOS with Python 3.11

## Architecture

```text
Telegram
   ↓
Telegram Bot
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
Codex CLI
```

## Commands

### Agents

```text
/agent create <name>
/agent list
/agent delete <name>
```

### Task Execution

```text
/run <agent> <task>
/continue <agent>
/retry <agent>
/queue <agent>
/queue clear <agent>
```

### Git

```text
/diff <agent>
/commit <agent> "message"
/push <agent>
/pr <agent> "title"
```

### Repositories

```text
/repo add <name> <path>
/repo list
/repo switch <agent> <repo>
```

### Monitoring

```text
/logs <agent>
/status
```

### Input

- Send a file to stage it into the active repo
- Send a voice message to transcribe it into a Codex task

## Project Structure

```text
codex-telegram-mcp/
├── main.py
├── .env
├── .env.example
├── requirements.txt
├── config.py
├── bot/
│   ├── __init__.py
│   ├── app.py
│   └── handlers.py
├── agents/
│   ├── __init__.py
│   ├── manager.py
│   ├── queue.py
│   ├── runner.py
│   ├── retry.py
│   └── watchdog.py
├── tmux/
│   ├── __init__.py
│   └── controller.py
├── git/
│   ├── __init__.py
│   ├── operations.py
│   └── pr.py
├── repo/
│   ├── __init__.py
│   └── manager.py
├── voice/
│   ├── __init__.py
│   └── transcribe.py
├── files/
│   ├── __init__.py
│   └── uploader.py
└── logs/
```

## Requirements

- Python 3.11+
- macOS
- `tmux`
- OpenAI Codex CLI installed and available as `codex`
- Telegram bot token
- Optional: `gh` CLI for PR creation
- Optional: Whisper runtime for voice transcription

## Configuration

Create a `.env` file in the repository root:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
ALLOWED_USER_IDS=[123456789]
DEFAULT_REPO_PATH=/absolute/path/to/default/repo
GITHUB_TOKEN=your_github_token
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running

```bash
python main.py
```

The bot runs with long polling and manages Codex through local `tmux` sessions.

## How It Works

1. Create one or more agents in Telegram.
2. Bind each agent to a repository or use a default repo path.
3. Send tasks to agents with `/run`.
4. Codex executes inside the agent’s dedicated `tmux` session.
5. Logs are streamed back to Telegram and stored locally.
6. Use git commands to inspect, commit, push, and open PRs from Telegram.
7. Upload files or send voice messages to turn external input into agent tasks.

## Use Cases

- Run Codex remotely from your phone while away from your machine
- Keep separate agents for backend, frontend, infra, or experiments
- Manage multiple repositories from one Telegram interface
- Review logs and task progress without opening a terminal
- Turn voice notes into actionable coding prompts

## License

This project is licensed under the terms in [LICENSE](/Users/murat/Desktop/codex-telegram-mcp/LICENSE).

Development is still in progress and some implementation details may continue to evolve.
