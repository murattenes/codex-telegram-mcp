Core agent system

* multiple agents
* per-agent queue
* tmux persistent sessions
* MCP router
* streaming logs

Execution features

* agent auto-retry
* continue last task
* diff preview
* git commit after task
* git push to repo/branch
* PR generation

Input features

* file upload
* voice message → Codex

Repo features

* multi-repo support
* repo switching
* branch management

---

# Final Architecture

```
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

---

# Commands (final design)

Agents

```
/agent create backend
/agent create frontend
/agent list
/agent delete backend
```

Run tasks

```
/run backend fix auth bug
/run frontend redesign navbar
```

Queue

```
/queue backend
/queue clear backend
```

Retry

```
/retry backend
```

Continue

```
/continue backend
```

Git

```
/commit backend "fix auth bug"
/push backend
/pr backend "Fix auth bug"
```

Diff preview

```
/diff backend
```

Repos

```
/repo list
/repo switch backend my-repo
/repo add my-repo path
```

Logs

```
/logs backend
/status
```

Upload
(send file)

```
/run backend fix uploaded file
```

Voice
(send voice message)

```
voice -> text -> /run backend <text>
```

---

# Repo Structure (final)

```
telegram-codex-agents/
│
├── bot/
│   ├── telegram_bot.py
│   └── handlers.py
│
├── agents/
│   ├── manager.py
│   ├── queue.py
│   ├── retry.py
│   └── state.py
│
├── tmux/
│   └── controller.py
│
├── git/
│   ├── commit.py
│   ├── diff.py
│   ├── pr.py
│   └── push.py
│
├── repo/
│   └── manager.py
│
├── voice/
│   └── transcribe.py
│
├── files/
│   └── uploader.py
│
├── config.yaml
├── main.py
└── README.md
```

---

# Feature behavior

Agent auto-retry

* if codex exits with error
* retry N times
* exponential backoff

Continue last task

* store last prompt per agent
* resend to codex
* keep context

Diff preview

```
git diff --staged
```

send to telegram

Git commit after task
Flow:

```
codex finishes
git add .
git commit -m "task"
```

PR generation
Flow:

```
create branch
commit
push
gh pr create
```

Multi-repo support
Each agent bound to repo:

```
backend -> repo1
frontend -> repo2
tests -> repo3
```

tmux sessions:

```
codex-backend
codex-frontend
codex-tests
```

---