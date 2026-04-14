"""Exact-match classifier for the four bot-side git fast-paths."""

from dataclasses import dataclass
from enum import Enum


class IntentKind(str, Enum):
    """Kinds of intents the bot recognizes in plain text."""

    PASSTHROUGH = "passthrough"
    DIFF = "diff"
    COMMIT = "commit"
    PUSH = "push"
    PR = "pr"


@dataclass
class Intent:
    """Classified plain-text input."""

    kind: IntentKind
    payload: str = ""


COMMIT_PREFIX = "commit it:"
PR_PREFIX = "open a pr:"
DIFF_EXACT = "show me the diff"
PUSH_EXACT = "push it"


def classify(text: str) -> Intent:
    """Return the intent for a plain-text message.

    Matching is case-insensitive and ignores leading/trailing whitespace.
    Anything that does not match one of the four rules becomes PASSTHROUGH
    so Codex handles the interpretation.
    """

    t = text.strip()
    lower = t.lower()

    if lower == DIFF_EXACT:
        return Intent(kind=IntentKind.DIFF)

    if lower == PUSH_EXACT:
        return Intent(kind=IntentKind.PUSH)

    if lower.startswith(COMMIT_PREFIX):
        msg = t[len(COMMIT_PREFIX):].strip()
        if msg:
            return Intent(kind=IntentKind.COMMIT, payload=msg)
        return Intent(kind=IntentKind.PASSTHROUGH)

    if lower.startswith(PR_PREFIX):
        title = t[len(PR_PREFIX):].strip()
        if title:
            return Intent(kind=IntentKind.PR, payload=title)
        return Intent(kind=IntentKind.PASSTHROUGH)

    return Intent(kind=IntentKind.PASSTHROUGH)
