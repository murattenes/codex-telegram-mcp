# Project Progress & Handoff Notes

Context document for resuming work in a new chat session without losing history.

---

## Project Goal
Telegram bot that controls OpenAI's Codex CLI remotely. Python 3.11, macOS, local only. Designed for phone-first use: select an agent once per chat, then talk naturally.

## Design Docs
- `IMPLEMENTATION_PLAN.md` — **current source of truth** (v2: chat-first hybrid)
- `plan.md` — original rough notes (kept for history, superseded)
- `prompt.md` — original build prompt (kept for history)

---

## What's Implemented (Phases 1–3 DONE, command-first v1)

The current code is a **working command-first bot**. It runs end-to-end but uses slash commands for everything. Phases 4–5 were never built; instead we pivoted to a cleaner chat-first design (v2).

### Phase 1 — Foundation (committed)
- Pydantic settings loaded from `.env` (`config.py`)
- `tmux/controller.py` — async tmux session CRUD, `pipe-pane` log capture
- `agents/manager.py` — Agent dataclass, `AgentManager` with create/delete/list
- `agents/runner.py` — runs `codex --full-auto` in tmux, polls log file for completion markers
- `bot/handlers.py` — `/start`, `/agent create|list|delete`, `/run`, `/status`, `/logs`
- `bot/app.py` — Telegram bot wiring with long polling
- `main.py` — entry point with dotenv + logging
- Whitelist auth via `ALLOWED_USER_IDS`

### Phase 2 — Queue, retry, watchdog (committed)
- `agents/queue.py` — per-agent FIFO `asyncio.Queue` with consumer loop
- `agents/retry.py` — auto-retry up to 3x with exponential backoff
- `agents/watchdog.py` — idle-timeout monitor (180s) kills hung tmux sessions, 30min TTL cleanup for idle sessions, 60s poll interval
- Added `/continue`, `/retry`, `/queue`, `/queue clear` commands
- `/run` auto-queues when agent busy

### Phase 3 — Git + PRs (committed)
- `git/operations.py` — async wrappers for `status`, `diff`, `staged diff`, `commit`, `push`, `create_branch`, `current branch`
- `git/pr.py` — `create_pr()` via `gh` CLI: auto-creates `codex/<slug>` branch, commits, pushes, opens PR
- Added `/diff`, `/commit`, `/push`, `/pr` commands

### Infrastructure
- Virtualenv at `.venv` (managed with `uv`)
- `.env.example` template with token, user IDs, default repo path
- `logs/` dir gitignored, used by `pipe-pane` output
- Whitelist, watchdog, rate-limit-safe log tailing all in place

---

## Pivot to v2: Chat-First Hybrid (CURRENT DIRECTION)

User feedback: command-first UX is hostile on mobile. Redesigned around:

- **One active agent per Telegram chat.** Set with `/use`, persists across restart.
- **Plain text = default path to Codex.** No `/run` needed.
- **Bot is dumb transport** with a tiny exact-match table.
- **4 fast-paths only** (no regex, no broad NL interpretation):
  - `show me the diff`
  - `commit it: <message>`
  - `push it`
  - `open a pr: <title>`
- **Backup commands** stay for the same actions: `/diff`, `/commit <msg>`, `/push`, `/pr <title>`
- **Everything else → Codex**, unchanged.
- **Confirmations** via inline buttons for push + PR only (not commit).
- **No live message editing** in v1 — just start/end messages.
- **No escape hatches** (`>`, `!`) in v1.
- **No undo-commit** in v1.

Full details in `IMPLEMENTATION_PLAN.md`. Acceptance criteria and edge cases are enumerated there.

### Files to be created (v2 Phase B)
1. `bot/chat_state.py` — per-chat active-agent state, persisted to `state/chat_state.json`
2. `bot/classifier.py` — 4 exact-match rules returning `Intent`
3. `bot/git_actions.py` — thin wrappers over `git/operations.py` + Telegram rendering
4. `bot/confirmations.py` — TTL store for inline confirm buttons
5. `bot/router.py` — `MessageRouter.handle_text` — classify + dispatch
6. `state/` directory (gitignored)

### Files to be rewritten/modified
- `bot/handlers.py` — shrink to new small command set; delete old `/run`, `/continue`, `/retry`, `/queue`, `/commit`, `/push`, `/pr`, `/diff`, old `/agent` dispatcher
- `bot/app.py` — register new commands + `MessageHandler(TEXT & ~COMMAND)` + `CallbackQueryHandler`
- `.gitignore` — add `state/`

### Files unchanged
Everything below the bot layer is reused as-is: `config.py`, `tmux/controller.py`, `agents/*.py`, `git/operations.py`, `git/pr.py`.

### New v1 command set
`/start`, `/help`, `/new <name> [repo]`, `/use <name>`, `/agents`, `/delete <name>`, `/status`, `/logs`, `/stop`, `/diff`, `/commit <msg>`, `/push`, `/pr <title>`

### Decisions confirmed with user
1. Busy agent: `show me the diff` runs anyway; commit/push/PR rejected.
2. Fast-path matching is case-insensitive + trimmed.
3. `/commit <msg>` command included as backup, because same reasoning as `/push` + `/pr`.
4. Plain text with multiple agents and no active set → user is told to pick; message is NOT buffered or auto-replayed.

---

## Migration Steps (v2 implementation order)

Each step is independently runnable and commitable.

1. Create `state/` + gitignore entry
2. `bot/chat_state.py`
3. `bot/classifier.py`
4. `bot/git_actions.py`
5. `bot/confirmations.py`
6. `bot/router.py`
7. Rewrite `bot/handlers.py`
8. Update `bot/app.py`
9. Manual test on phone

User preference: one commit per step (or at most per logical pair). User handles commits and pushes personally.

---

## How to Run

```bash
uv venv .venv                         # first time only
uv pip install -r requirements.txt    # first time only
cp .env.example .env                  # fill in values
.venv/bin/python main.py              # start bot
```

Required tools on `$PATH`: `tmux`, `codex`, `gh` (authenticated via `gh auth login`).

---

## Memory Notes
- User is `murattenes`, repo `codex-telegram-mcp` on GitHub, working on macOS.
- User commits + pushes personally after each phase. Never push from tool calls.
- Always syntax-check Python files via `.venv/bin/python -m py_compile` instead of installing packages globally.
- User will typically test each phase on phone before moving on.
- Editor/linter may add docstrings after writes — that's expected, don't revert.