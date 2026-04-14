# Telegram Codex Agent - Implementation Plan (v2: Chat-First Hybrid)

## Context
Build a Telegram bot that controls OpenAI's Codex CLI remotely via tmux sessions.
Python 3.11, macOS, local only. Designed for phone-first use.

## Core Idea
- In any Telegram chat, one agent is "active."
- Plain text messages are forwarded to the active agent's Codex session.
- Slash commands are used only for session/bot control.
- A tiny, exact-match fast-path lets a few high-value git actions be handled directly by the bot instead of going through Codex.
- Codex remains responsible for interpreting real tasks. The bot is dumb transport with a 4-rule shortcut table.

## Mental Model
- `/...` talks to the **bot** (session management)
- plain text talks to the **agent** (Codex)
- 4 exact phrases talk to the **bot's git helpers**

---

## Architecture

```
Telegram Update
      ↓
Auth middleware
      ↓
   Is command?  ──yes──> command handler
      │
      no
      ↓
   Is callback_query?  ──yes──> inline button handler
      │
      no
      ↓
MessageRouter
   ├── Classifier (4 exact rules)
   │     ├── DIFF         → git_actions.diff
   │     ├── COMMIT(msg)  → git_actions.commit
   │     ├── PUSH         → confirmations → git_actions.push
   │     ├── PR(title)    → confirmations → git_actions.pr
   │     └── PASSTHROUGH  → agents/runner (via queue)
```

Nothing below the router changes. Runner, queue, retry, watchdog, git ops, PR helper — all reused as-is.

---

## Commands (v1, final)

| Command | Purpose |
|---|---|
| `/start` | Welcome + help |
| `/help` | Command reference |
| `/new <name> [repo_path]` | Create agent. Repo optional, defaults to `DEFAULT_REPO_PATH`. |
| `/use <name>` | Set active agent for this chat |
| `/agents` | List agents with inline "select" buttons |
| `/delete <name>` | Delete an agent |
| `/status` | Summary of all agents + which is active in this chat |
| `/logs` | Tail active agent's log |
| `/stop` | Cancel currently running task on active agent |
| `/diff` | Git diff (backup for "show me the diff") |
| `/commit <message>` | Commit (backup for "commit it: ...") |
| `/push` | Git push (backup for "push it") |
| `/pr <title>` | Create PR (backup for "open a pr: ...") |

**Removed from v1:** `/run`, `/continue`, `/retry`, `/queue`, old `/agent` dispatcher.

---

## Fast-Paths (v1, exact-match)

Matching rules:
- `text.strip().lower()` before comparing
- Whitespace and case do not matter
- `commit it:` and `open a pr:` use a prefix match; the suffix is free text
- Anything else → passthrough to Codex

| Input | Action |
|---|---|
| `show me the diff` | git diff, post formatted |
| `commit it: <message>` | stage all + commit with `<message>` |
| `push it` | show `[Confirm push] [Cancel]` → on confirm, git push |
| `open a pr: <title>` | show `[Confirm PR] [Cancel]` → on confirm, commit→push→gh pr create |

No regex engine. No broad natural-language interpretation. Only these four rules.

### Classifier (reference implementation)
```python
def classify(text: str) -> Intent:
    t = text.strip()
    lower = t.lower()

    if lower == "show me the diff":
        return Intent(kind=DIFF)
    if lower == "push it":
        return Intent(kind=PUSH)
    if lower.startswith("commit it:"):
        msg = t[len("commit it:"):].strip()
        return Intent(kind=COMMIT, payload=msg) if msg else Intent(kind=PASSTHROUGH)
    if lower.startswith("open a pr:"):
        title = t[len("open a pr:"):].strip()
        return Intent(kind=PR, payload=title) if title else Intent(kind=PASSTHROUGH)

    return Intent(kind=PASSTHROUGH)
```

---

## Active Agent Selection

Per-chat state: `chat_id -> active_agent_name`. Persisted to `state/chat_state.json`.

Ways to select:
1. `/use <name>` — explicit
2. `/agents` → inline button → one tap
3. `/new <name>` response includes `[Use this agent]` button

Plain text with no active agent:
- **0 agents** → `No agents. Create one with /new <name>.`
- **1 agent** → auto-select, proceed
- **2+ agents** → `Multiple agents available. Pick one with /use <name> or /agents, then send your message again.` **The original message is NOT buffered and NOT auto-replayed.**

---

## Confirmations

Used only for network-visible destructive actions.

| Action | Confirmation |
|---|---|
| `commit it: <msg>` / `/commit <msg>` | none, auto-execute |
| `push it` / `/push` | `[Confirm push] [Cancel]`, TTL 2 min |
| `open a pr: <title>` / `/pr <title>` | `[Confirm PR: <title>] [Cancel]`, TTL 2 min |

Expired callback → `Confirmation expired, try again`.
No undo-commit in v1.

---

## Live Output (v1: minimum viable)

- On task start: send `⏳ Running on '<agent>': <first 100 chars>`
- On success: send `✅ Done` + last ~30 log lines in code block
- On failure: send `❌ Failed` + error tail + inline `[Retry]` button
- On watchdog kill: push new message `⏱ '<agent>' hung, session restarted` + `[Retry]`

No periodic message editing in v1. If silence during long runs feels bad in practice, add it in v2.

---

## Busy Agent Behavior

When a task is already running on the active agent:
- Plain text → enqueue. Reply `📥 Queued on '<agent>' (position N)`.
- `show me the diff` / `/diff` → run anyway (read-only, safe).
- `commit it:` / `push it` / `open a pr:` / `/commit` / `/push` / `/pr` → reject with `⚠️ Agent busy, try again when done.`

When queued task completes, post its result message to the same chat that queued it.

---

## Multi-Repo

Each agent is bound to one repo at creation (`/new backend ~/code/app`).
Switching repos = switching active agent. No repo registry or `/repo` commands in v1.

---

## State Model

### Persisted
```json
// state/chat_state.json
{
  "<chat_id>": {"active_agent": "backend"}
}
```

### In-memory, TTL-based
```python
# pending confirmations, key = callback_data id
{
  "cfrm_<uuid>": {
    "action": "push" | "pr",
    "agent": "backend",
    "chat_id": 12345,
    "payload": {"title": "..."},  # for pr only
    "expires_at": 1712968200
  }
}
```

No new fields on the `Agent` dataclass in v1.

---

## Files

### New
| File | Purpose |
|---|---|
| `bot/chat_state.py` | `ChatStateManager` with JSON persistence |
| `bot/classifier.py` | 4 exact-match rules, returns `Intent` |
| `bot/router.py` | `MessageRouter.handle_text()` — classify + dispatch |
| `bot/git_actions.py` | Thin wrappers over `git/operations.py` + `git/pr.py` with Telegram rendering |
| `bot/confirmations.py` | Pending-confirmation store with TTL + callback-button helpers |
| `state/` | Gitignored state directory |

### Modified
| File | Change |
|---|---|
| `bot/handlers.py` | Shrink to v1 command set. Delete old `/run`, `/continue`, `/retry`, `/queue`, `/commit`, `/push`, `/pr`, `/diff`, old `/agent` dispatcher. Add new `/new`, `/use`, `/agents`, `/delete`, `/stop`, and the git backup commands. |
| `bot/app.py` | Register new command handlers + `MessageHandler(filters.TEXT & ~filters.COMMAND, router.handle_text)` + `CallbackQueryHandler` for inline buttons. |
| `.gitignore` | Add `state/` |

### Unchanged
`config.py`, `tmux/controller.py`, `agents/manager.py`, `agents/runner.py`, `agents/queue.py`, `agents/retry.py`, `agents/watchdog.py`, `git/operations.py`, `git/pr.py`

---

## Migration Path

Each step compiles and runs cleanly on its own.

1. Create `state/` directory + gitignore entry.
2. Create `bot/chat_state.py` (ChatStateManager with load/save).
3. Create `bot/classifier.py` (4 rules, Intent dataclass, unit-testable).
4. Create `bot/git_actions.py` (diff/commit/push/pr wrappers + formatters).
5. Create `bot/confirmations.py` (TTL store + button builders).
6. Create `bot/router.py` (MessageRouter.handle_text, callback dispatch).
7. Rewrite `bot/handlers.py` — new v1 command set only.
8. Update `bot/app.py` — register new handlers, MessageHandler, CallbackQueryHandler.
9. Manual test on phone.

---

## Acceptance Criteria

- [ ] `/new backend ~/code/app` creates an agent and offers `[Use this agent]` button
- [ ] `/use backend` sets active agent, persists across bot restart
- [ ] `/agents` shows inline list; tapping a button selects the agent
- [ ] Plain text with active agent → task runs on that agent, final result posted
- [ ] Plain text with zero agents → told to create one
- [ ] Plain text with one agent and no active set → auto-selects and runs
- [ ] Plain text with multiple agents and no active set → told to pick, original message NOT auto-replayed
- [ ] `show me the diff` → executes git diff, Telegram-formatted output
- [ ] `commit it: fix login` → commits and returns hash/message
- [ ] `push it` → shows confirmation buttons → on confirm, pushes
- [ ] `open a pr: fix login` → shows confirmation buttons → on confirm, commits+pushes+creates PR
- [ ] `/diff`, `/commit <msg>`, `/push`, `/pr <title>` work as equivalents for their fast-paths
- [ ] Any text that is not an exact fast-path is forwarded to Codex unchanged
- [ ] Busy agent + plain text → queued with position
- [ ] Busy agent + `show me the diff` → runs anyway
- [ ] Busy agent + `commit it:` / `push it` / `open a pr:` → rejected with "agent busy"
- [ ] Confirmation callback buttons expire after 2 minutes
- [ ] Failed task → `❌ Failed` message with `[Retry]` button
- [ ] Watchdog kill → pushes `⏱` message to the chat that started the task
- [ ] `/stop` cancels the running task on the active agent
- [ ] No `/run`, `/continue`, `/retry`, `/queue` commands exist

---

## Edge Cases

- Bot restart mid-task: active-agent state persists; tmux session still alive; on restart, no live message pointer exists but logs can be read with `/logs`.
- Whisper / file upload (future phases) feed into `MessageRouter.handle_text` so they automatically benefit from fast-paths and passthrough without new logic.
- Codex prompts for interactive input despite `--full-auto`: watchdog catches it via idle timeout.
- Two chats using the same agent: allowed, share the queue, each queued task's result message posts back to whichever chat enqueued it.
- Very long diff (> 50KB): first chunk posted, rest truncated with a `[use /logs for full tail]` note.
- Force-push is never supported.

---

## Out of Scope (v1)

- Periodic progress editing of the "Running..." message
- Escape-hatch syntax (`>` forced passthrough, `!` forced bot interpretation)
- Undo-commit button
- Repo registry / `/repo` commands
- Multi-user whitelist UI (static `.env` list is enough)
- Broad natural-language intent detection

These may be revisited in v2 based on real usage pain.

---

## Future Phases (unchanged, still after v1)

- **Voice Input:** voice message → Whisper → `MessageRouter.handle_text`
- **File Upload:** file saved to active agent's repo → next plain text message becomes the task
- **Repo Bookmarks:** optional shorthand `@repo-name` registry
