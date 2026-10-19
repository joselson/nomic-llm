"""Agent provider interface for the night compiler.

A provider turns the compile conversation into model calls. The compiler only
talks to an AgentSession: send text in, get an AgentReply out. Add a provider
by implementing AgentProvider and registering it in agents/__init__.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from game.dsl import Mechanic

ReplyKind = Literal["submit", "reject", "none", "refused", "error", "unconfigured"]


@dataclass
class AgentReply:
    kind: ReplyKind
    mechanic: dict | None = None       # raw Mechanic JSON for "submit"
    reason: str | None = None          # rejection reason / problem description
    usage: dict[str, Any] = field(default_factory=dict)


class AgentSession(Protocol):
    async def send(self, text: str) -> AgentReply:
        """Send the next user turn (the idea, or feedback on the last submission)."""
        ...


class AgentProvider(Protocol):
    name: str
    model: str

    def session(self) -> AgentSession: ...

    async def warmup(self) -> None:
        """Optional: prepare before a burst of parallel sessions (e.g. warm a prompt cache)."""
        ...


def _strip_titles(node: Any, in_properties: bool = False) -> Any:
    """Drop Pydantic's auto-generated `title`s (~20% of the schema, no information)."""
    if isinstance(node, dict):
        return {k: _strip_titles(v, k == "properties") for k, v in node.items()
                if in_properties or not (k == "title" and isinstance(v, str))}
    if isinstance(node, list):
        return [_strip_titles(x) for x in node]
    return node


def mechanic_schema() -> dict:
    return _strip_titles(Mechanic.model_json_schema(by_alias=True))


def add_usage(total: dict, part: dict) -> None:
    for k, v in part.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total[k] = round(total.get(k, 0) + v, 6)
