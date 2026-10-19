"""Idea → Mechanic via Claude, with a validate/simulate retry loop (SPEC §5.1)."""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

import anthropic
from pydantic import ValidationError

from game.dsl import Mechanic, validate_mechanic

from .night import CompileResult
from .prompts import REJECT_TOOL_DESCRIPTION, SUBMIT_TOOL_DESCRIPTION, SYSTEM_PROMPT, user_message
from .simulate import simulate
from .world_summary import world_summary

log = logging.getLogger("compiler")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _strip_titles(node: Any, in_properties: bool = False) -> Any:
    """Drop Pydantic's auto-generated `title`s (~20% of the schema, no information)."""
    if isinstance(node, dict):
        return {k: _strip_titles(v, k == "properties") for k, v in node.items()
                if in_properties or not (k == "title" and isinstance(v, str))}
    if isinstance(node, list):
        return [_strip_titles(x) for x in node]
    return node


def build_tools() -> list[dict]:
    return [
        {"name": "submit_mechanic", "description": SUBMIT_TOOL_DESCRIPTION,
         "input_schema": _strip_titles(Mechanic.model_json_schema(by_alias=True))},
        {"name": "reject_idea", "description": REJECT_TOOL_DESCRIPTION,
         "input_schema": {"type": "object", "additionalProperties": False, "required": ["reason"],
                          "properties": {"reason": {"type": "string", "maxLength": 300}}}},
    ]


TOOLS = build_tools()
SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _fmt_validation(e: ValidationError, limit: int = 12) -> list[str]:
    out = []
    for err in e.errors()[:limit]:
        loc = "/".join(str(x) for x in err.get("loc", ()))
        out.append(f"{loc}: {err.get('msg')}")
    if len(e.errors()) > limit:
        out.append(f"… and {len(e.errors()) - limit} more")
    return out


class Compiler:
    """One night's compiler: all ideas compile against the same snapshot of the live world."""

    def __init__(self, client: anthropic.AsyncAnthropic, *, model: str, live: list[Mechanic], summary: str,
                 live_day: int, players: int, map_size: int, points_cap: float, max_entities: int,
                 max_attempts: int = 3, sim_seconds: float = 120, effort: str = "medium",
                 max_tokens: int = 16000, fallbacks: bool = True) -> None:
        self.client = client
        self.model = model
        self.live = live
        self.summary = summary
        self.live_day = live_day
        self.players = players
        self.map_size = map_size
        self.points_cap = points_cap
        self.max_entities = max_entities
        self.max_attempts = max_attempts
        self.sim_seconds = sim_seconds
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks

    # ------------------------------------------------------------------ #
    async def _create(self, messages: list, max_tokens: int | None = None):
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
            await self._create([{"role": "user", "content": "warmup"}], max_tokens=0)
        except (anthropic.APIError, TypeError) as e:
            log.warning("cache warmup failed: %s", e)

    # ------------------------------------------------------------------ #
    async def __call__(self, idea: str) -> CompileResult:
        messages: list[dict] = [{"role": "user", "content": user_message(idea, self.summary)}]
        usage = {"model": self.model, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0, "requests": 0}
        errors: list[str] = []
        t0 = time.monotonic()
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = await self._create(messages)
            except (anthropic.AuthenticationError, TypeError):
                # TypeError: the SDK found no credentials (no key, token or profile)
                return CompileResult("infeasible", reason="The idea compiler isn't configured on this server.",
                                     attempts=attempt, errors=errors, usage=usage)
            except anthropic.APIError as e:
                log.warning("compile request failed: %s", e)
                return CompileResult("infeasible", reason="The idea compiler is unavailable right now.",
                                     attempts=attempt, errors=errors + [str(e)[:300]], usage=usage)
            self._add_usage(usage, resp)
            if resp.stop_reason == "refusal":
                return CompileResult("infeasible", reason="The compiler declined to build this idea.",
                                     attempts=attempt, errors=errors, usage=usage)
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": resp.content})
            chosen = next((b for b in tool_uses if b.name in ("submit_mechanic", "reject_idea")), None)

            if chosen is not None and chosen.name == "reject_idea" and resp.stop_reason != "max_tokens":
                reason = str((chosen.input or {}).get("reason") or "Not buildable as a game mechanic.")[:300]
                return CompileResult("infeasible", reason=reason, attempts=attempt, errors=errors, usage=usage)

            if chosen is None or resp.stop_reason == "max_tokens":
                problem = ("Your response was cut off before the mechanic was complete. Submit a smaller mechanic."
                           if resp.stop_reason == "max_tokens"
                           else "Respond by calling submit_mechanic or reject_idea.")
                errors.append(problem)
                messages.append({"role": "user", "content": self._results(tool_uses, None, problem)
                                 or [{"type": "text", "text": problem}]})
                continue

            mechanic, problems = await self._check(chosen.input)
            usage["latency_s"] = round(time.monotonic() - t0, 2)
            if mechanic is not None:
                return CompileResult("ok", mechanic, attempts=attempt, errors=errors, usage=usage)
            errors.extend(problems)
            feedback = ("The game server could not accept this mechanic:\n" + "\n".join(f"- {p}" for p in problems)
                        + "\nFix every problem and call submit_mechanic again with the complete mechanic, "
                          "or reject_idea if it cannot be built.")
            messages.append({"role": "user", "content": self._results(tool_uses, chosen.id, feedback)})
        usage["latency_s"] = round(time.monotonic() - t0, 2)
        last = errors[-1] if errors else "unknown problem"
        return CompileResult("infeasible", reason=f"It couldn't be built within the rules ({last[:160]}).",
                             attempts=self.max_attempts, errors=errors, usage=usage)

    @staticmethod
    def _results(tool_uses: list, chosen_id: str | None, feedback: str) -> list[dict]:
        """Every tool_use needs a tool_result; all go back in one user message."""
        out = []
        for b in tool_uses:
            text = feedback if b.id == chosen_id or chosen_id is None else "Ignored: call exactly one tool."
            out.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": True})
        return out

    async def _check(self, raw: Any) -> tuple[Mechanic | None, list[str]]:
        if not isinstance(raw, dict):
            return None, ["the tool input must be a Mechanic object"]
        data = copy.deepcopy(raw)
        data["creator"] = "pending"
        data["day"] = self.live_day
        data.pop("creator_perk", None)
        existing = {m.id for m in self.live}
        if isinstance(data.get("id"), str) and data["id"] in existing:
            base, n = data["id"][:36], 2
            while f"{base}_{n}" in existing:
                n += 1
            data["id"] = f"{base}_{n}"
        try:
            m = Mechanic.model_validate(data)
        except ValidationError as e:
            return None, _fmt_validation(e)
        errors, _warnings = validate_mechanic(m, list(self.live))
        if errors:
            return None, errors[:15]
        report = await asyncio.to_thread(
            simulate, self.live, m, players=self.players, seconds=self.sim_seconds, map_size=self.map_size,
            day=self.live_day, points_cap=self.points_cap, max_entities=self.max_entities)
        if not report.ok:
            return None, report.errors[:10]
        return m, []

    @staticmethod
    def _add_usage(usage: dict, resp) -> None:
        u = getattr(resp, "usage", None)
        usage["requests"] += 1
        if u is None:
            return
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[k] += getattr(u, k, 0) or 0


def make_compile_factory(cfg):
    """Return factory(room) -> Compiler, or None when the SDK can't be set up."""
    try:
        client = anthropic.AsyncAnthropic(timeout=cfg.compile_deadline_s, max_retries=1)
    except Exception as e:  # noqa: BLE001 — e.g. no credentials configured
        log.warning("idea compiler disabled: %s", e)
        return None

    def factory(room) -> Compiler:
        w = room.world
        live = list(w.mechanics.values())
        players = max(1, len(w.players))
        summary = world_summary(live, day=room.day + 1, player_count=players, map_size=w.map_size)
        return Compiler(client, model=cfg.compile_model, live=live, summary=summary, live_day=room.day + 1,
                        players=players, map_size=w.map_size, points_cap=cfg.points_cap_per_mechanic,
                        max_entities=cfg.max_entities, max_attempts=cfg.compile_max_attempts,
                        sim_seconds=cfg.sim_seconds, effort=cfg.compile_effort,
                        fallbacks=cfg.compile_fallbacks)
    return factory

