"""Idea → Mechanic with a validate/simulate retry loop (SPEC §5.1).

The model call itself is delegated to a pluggable agent provider (see agents/).
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

from pydantic import ValidationError

from game.dsl import Mechanic, validate_mechanic

from .agents import AgentProvider, make_provider
from .agents.base import add_usage
from .night import CompileResult
from .prompts import user_message
from .simulate import simulate
from .world_summary import world_summary

log = logging.getLogger("compiler")


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

    def __init__(self, provider: AgentProvider, *, live: list[Mechanic], summary: str, live_day: int,
                 players: int, map_size: int, points_cap: float, max_entities: int, max_attempts: int = 3,
                 sim_seconds: float = 120) -> None:
        self.provider = provider
        self.live = live
        self.summary = summary
        self.live_day = live_day
        self.players = players
        self.map_size = map_size
        self.points_cap = points_cap
        self.max_entities = max_entities
        self.max_attempts = max_attempts
        self.sim_seconds = sim_seconds

    async def warmup(self) -> None:
        await self.provider.warmup()

    async def __call__(self, idea: str) -> CompileResult:
        session = self.provider.session()
        usage: dict[str, Any] = {"provider": self.provider.name, "model": self.provider.model}
        errors: list[str] = []
        t0 = time.monotonic()

        def result(status: str, mechanic: Mechanic | None = None, reason: str | None = None,
                   attempts: int = 0) -> CompileResult:
            usage["latency_s"] = round(time.monotonic() - t0, 2)
            return CompileResult(status, mechanic, reason=reason, attempts=attempts, errors=errors, usage=usage)

        message = user_message(idea, self.summary)
        for attempt in range(1, self.max_attempts + 1):
            reply = await session.send(message)
            add_usage(usage, reply.usage)
            if reply.kind == "unconfigured":
                return result("infeasible", reason="The idea compiler isn't configured on this server.",
                              attempts=attempt)
            if reply.kind == "error":
                errors.append(reply.reason or "provider error")
                return result("infeasible", reason="The idea compiler is unavailable right now.", attempts=attempt)
            if reply.kind == "refused":
                return result("infeasible", reason="The compiler declined to build this idea.", attempts=attempt)
            if reply.kind == "reject":
                return result("infeasible", reason=reply.reason or "Not buildable as a game mechanic.",
                              attempts=attempt)
            if reply.kind == "none":
                message = reply.reason or "Respond with a submission or a rejection."
                errors.append(message)
                continue
            mechanic, problems = await self._check(reply.mechanic)
            if mechanic is not None:
                return result("ok", mechanic, attempts=attempt)
            errors.extend(problems)
            message = ("The game server could not accept this mechanic:\n" + "\n".join(f"- {p}" for p in problems)
                       + "\nFix every problem and submit the complete corrected mechanic again, "
                         "or reject the idea if it cannot be built.")
        last = errors[-1] if errors else "unknown problem"
        return result("infeasible", reason=f"It couldn't be built within the rules ({last[:160]}).",
                      attempts=self.max_attempts)

    async def _check(self, raw: Any) -> tuple[Mechanic | None, list[str]]:
        if not isinstance(raw, dict):
            return None, ["the submission must be a Mechanic object"]
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


def make_compile_factory(cfg):
    """Return factory(room) -> Compiler, or None when the configured provider can't be set up."""
    try:
        provider = make_provider(cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("idea compiler disabled: %s", e)
        return None
    log.info("idea compiler: provider=%s model=%s", provider.name, provider.model)

    def factory(room) -> Compiler:
        w = room.world
        live = list(w.mechanics.values())
        players = max(1, len(w.players))
        summary = world_summary(live, day=room.day + 1, player_count=players, map_size=w.map_size)
        return Compiler(provider, live=live, summary=summary, live_day=room.day + 1, players=players,
                        map_size=w.map_size, points_cap=cfg.points_cap_per_mechanic,
                        max_entities=cfg.max_entities, max_attempts=cfg.compile_max_attempts,
                        sim_seconds=cfg.sim_seconds)
    return factory
