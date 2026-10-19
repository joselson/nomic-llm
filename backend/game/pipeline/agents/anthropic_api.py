"""Provider: the Anthropic Messages API with submit/reject tools (needs an API key)."""
from __future__ import annotations

import logging
from typing import Any

import anthropic

from ..prompts import REJECT_TOOL_DESCRIPTION, SUBMIT_TOOL_DESCRIPTION, SYSTEM_PROMPT
from .base import AgentReply, mechanic_schema

log = logging.getLogger("agents.anthropic")

FALLBACK_BETA = "server-side-fallback-2026-07-01"

TOOLS = [
    {"name": "submit_mechanic", "description": SUBMIT_TOOL_DESCRIPTION, "input_schema": mechanic_schema()},
    {"name": "reject_idea", "description": REJECT_TOOL_DESCRIPTION,
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["reason"],
                      "properties": {"reason": {"type": "string", "maxLength": 300}}}},
]
SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


class AnthropicAPIProvider:
    name = "anthropic"

    def __init__(self, client: Any, *, model: str, effort: str = "medium", max_tokens: int = 16000,
                 fallbacks: bool = True) -> None:
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks

    @classmethod
    def from_config(cls, cfg) -> "AnthropicAPIProvider":
        client = anthropic.AsyncAnthropic(timeout=cfg.compile_deadline_s, max_retries=1)
        return cls(client, model=cfg.compile_model, effort=cfg.compile_effort, fallbacks=cfg.compile_fallbacks)

    async def create(self, messages: list, max_tokens: int | None = None):
        params: dict[str, Any] = dict(
            model=self.model, max_tokens=self.max_tokens if max_tokens is None else max_tokens,
            system=SYSTEM, tools=TOOLS, tool_choice={"type": "auto"},
            output_config={"effort": self.effort}, messages=messages,
        )
        if self.fallbacks:
            return await self.client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
        return await self.client.messages.create(**params)

    async def warmup(self) -> None:
        """Write the shared prefix (tools + system) to the cache before the parallel burst."""
        try:
            await self.create([{"role": "user", "content": "warmup"}], max_tokens=0)
        except (anthropic.APIError, TypeError) as e:
            log.warning("cache warmup failed: %s", e)

    def session(self) -> "AnthropicSession":
        return AnthropicSession(self)


class AnthropicSession:
    def __init__(self, provider: AnthropicAPIProvider) -> None:
        self.p = provider
        self.messages: list[dict] = []
        self.pending: list = []           # tool_use blocks awaiting tool_results

    async def send(self, text: str) -> AgentReply:
        if self.pending:
            # every tool_use needs a tool_result; all go back in one user message
            content: Any = [{"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": True}
                            for b in self.pending]
        else:
            content = text
        self.messages.append({"role": "user", "content": content})
        try:
            resp = await self.p.create(self.messages)
        except (anthropic.AuthenticationError, TypeError):
            # TypeError: the SDK found no credentials (no key, token or profile)
            return AgentReply("unconfigured", reason="The idea compiler isn't configured on this server.")
        except anthropic.APIError as e:
            log.warning("compile request failed: %s", e)
            return AgentReply("error", reason=str(e)[:300])
        usage = _usage(resp)
        if resp.stop_reason == "refusal":
            return AgentReply("refused", usage=usage)
        self.messages.append({"role": "assistant", "content": resp.content})
        self.pending = [b for b in resp.content if b.type == "tool_use"]
        chosen = next((b for b in self.pending if b.name in ("submit_mechanic", "reject_idea")), None)
        if resp.stop_reason == "max_tokens":
            return AgentReply("none", reason="Your response was cut off before the mechanic was complete. "
                                             "Submit a smaller mechanic.", usage=usage)
        if chosen is None:
            return AgentReply("none", reason="Respond by calling submit_mechanic or reject_idea.", usage=usage)
        if chosen.name == "reject_idea":
            reason = str((chosen.input or {}).get("reason") or "Not buildable as a game mechanic.")
            return AgentReply("reject", reason=reason[:300], usage=usage)
        return AgentReply("submit", mechanic=chosen.input if isinstance(chosen.input, dict) else None,
                          usage=usage)


def _usage(resp) -> dict:
    u = getattr(resp, "usage", None)
    out = {"requests": 1}
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        out[k] = (getattr(u, k, 0) or 0) if u is not None else 0
    return out
