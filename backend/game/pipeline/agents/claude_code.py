"""Provider: Claude Code in headless mode (`claude -p`), using whatever account the CLI is logged in with.

Each turn is one stateless CLI call: the prompt carries the idea plus every earlier
submission and the server's feedback. Structured output is enforced with --json-schema.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile

from ..prompts import JSON_OUTPUT, build_system_prompt
from .base import AgentReply, mechanic_schema

log = logging.getLogger("agents.claude_code")

SYSTEM_PROMPT = build_system_prompt(JSON_OUTPUT)


NON_STANDARD = {"discriminator"}     # OpenAPI keyword Pydantic emits; strict JSON Schema validators reject it


def _standard(node, in_properties: bool = False):
    if isinstance(node, dict):
        return {k: _standard(v, k == "properties") for k, v in node.items()
                if in_properties or k not in NON_STANDARD}
    if isinstance(node, list):
        return [_standard(x) for x in node]
    return node


def response_schema() -> dict:
    ms = _standard(mechanic_schema())
    defs = ms.pop("$defs", {})
    return {
        "type": "object", "additionalProperties": False, "required": ["decision"],
        "properties": {
            "decision": {"type": "string", "enum": ["submit", "reject"]},
            "reason": {"type": "string", "maxLength": 300},
            "mechanic": ms,
        },
        "$defs": defs,
    }


SCHEMA_JSON = json.dumps(response_schema(), separators=(",", ":"))
AUTH_HINTS = ("login", "log in", "logged in", "authenticat", "api key", "oauth", "/login", "credential")


class ClaudeCodeProvider:
    name = "claude_code"

    def __init__(self, *, bin: str = "claude", model: str = "claude-sonnet-5-5", effort: str | None = "medium",
                 timeout_s: float = 90, max_parallel: int = 4) -> None:
        self.bin = bin
        self.model = model
        self.effort = effort
        self.timeout_s = timeout_s
        self.max_parallel = max(1, max_parallel)
        self._sem: asyncio.Semaphore | None = None
        # an empty working directory: no CLAUDE.md, project settings or files for the CLI to pick up
        self.workdir = tempfile.mkdtemp(prefix="pn-claude-")

    @classmethod
    def from_config(cls, cfg) -> "ClaudeCodeProvider":
        return cls(bin=cfg.claude_code_bin, model=cfg.compile_model, effort=cfg.compile_effort,
                   timeout_s=cfg.compile_deadline_s, max_parallel=cfg.agent_max_parallel)

    async def warmup(self) -> None:
        return None

    def session(self) -> "ClaudeCodeSession":
        return ClaudeCodeSession(self)

    def args(self) -> list[str]:
        args = [self.bin, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                "--strict-mcp-config", "--disable-slash-commands", "--model", self.model,
                "--system-prompt", SYSTEM_PROMPT, "--json-schema", SCHEMA_JSON]
        if self.effort:
            args += ["--effort", self.effort]
        return args

    async def run(self, prompt: str) -> AgentReply:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_parallel)
        async with self._sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self.args(), cwd=self.workdir, env=os.environ.copy(),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
            except FileNotFoundError:
                return AgentReply("unconfigured", reason="The idea compiler isn't installed on this server.")
            try:
                out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=self.timeout_s)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.wait()
                raise
        return parse_result(out.decode(errors="replace"), err.decode(errors="replace"), proc.returncode)


def parse_result(stdout: str, stderr: str, returncode: int | None) -> AgentReply:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        text = (stderr or stdout).strip()[:300]
        kind = "unconfigured" if any(h in text.lower() for h in AUTH_HINTS) else "error"
        return AgentReply(kind, reason=text or f"claude exited with code {returncode}")
    usage = {"requests": 1, "cost_usd": data.get("total_cost_usd") or 0}
    u = data.get("usage") or {}
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        usage[k] = u.get(k) or 0
    if data.get("is_error") or data.get("subtype") != "success":
        text = str(data.get("result") or stderr or data.get("subtype") or "error")[:300]
        kind = "unconfigured" if any(h in text.lower() for h in AUTH_HINTS) else "error"
        return AgentReply(kind, reason=text, usage=usage)
    if data.get("stop_reason") == "refusal":
        return AgentReply("refused", usage=usage)
    out = data.get("structured_output")
    if out is None:
        try:
            out = json.loads(data.get("result") or "")
        except json.JSONDecodeError:
            out = None
    if not isinstance(out, dict):
        return AgentReply("none", reason="Respond with a JSON object that matches the schema.", usage=usage)
    if out.get("decision") == "reject":
        return AgentReply("reject", reason=str(out.get("reason") or "Not buildable as a game mechanic.")[:300],
                          usage=usage)
    if out.get("decision") == "submit" and isinstance(out.get("mechanic"), dict):
        return AgentReply("submit", mechanic=out["mechanic"], usage=usage)
    return AgentReply("none", reason='Respond with {"decision": "submit", "mechanic": {...}} or '
                                     '{"decision": "reject", "reason": "..."}.', usage=usage)


class ClaudeCodeSession:
    def __init__(self, provider: ClaudeCodeProvider) -> None:
        self.p = provider
        self.first: str | None = None
        self.turns: list[tuple[str, str]] = []     # (previous response JSON, server feedback)
        self.last: str | None = None

    async def send(self, text: str) -> AgentReply:
        if self.first is None:
            self.first = text
        else:
            self.turns.append((self.last or "(no valid response)", text))
        reply = await self.p.run(self.render())
        if reply.kind == "submit":
            self.last = json.dumps({"decision": "submit", "mechanic": reply.mechanic}, ensure_ascii=False,
                                   separators=(",", ":"))
        elif reply.kind == "none":
            self.last = None
        return reply

    def render(self) -> str:
        parts = [self.first or ""]
        for i, (prev, feedback) in enumerate(self.turns, 1):
            parts.append(f"<previous_response attempt=\"{i}\">{prev}</previous_response>\n"
                         f"<server_feedback attempt=\"{i}\">{feedback}</server_feedback>")
        if self.turns:
            parts.append("Respond again with the complete corrected JSON object.")
        return "\n\n".join(parts)
