"""Headless bot simulation of a candidate mechanic on a copy of the live world (SPEC §5.3)."""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from game.dsl import POINTS_CAP_PER_MECHANIC, Mechanic
from game.engine.world import World

DT = 0.05
THINK_S = 0.5
MAX_AVG_TICK_MS = 20.0


@dataclass
class SimReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fired: int = 0
    avg_tick_ms: float = 0.0

    def text(self) -> str:
        lines = ["Simulation " + ("passed." if self.ok else "failed:")]
        lines += [f"- {e}" for e in self.errors]
        lines += [f"- warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def simulate(live: list[Mechanic], candidate: Mechanic, *, players: int = 4, seconds: float = 120,
             map_size: int = 48, day: int = 1, points_cap: float = POINTS_CAP_PER_MECHANIC,
             max_entities: int = 1000, seed: int | None = None) -> SimReport:
    rng = random.Random(seed)
    w = World(map_size=map_size, seed=rng.randrange(1 << 30), points_cap=points_cap,
              max_entities=max_entities, win_points=1e9)
    n = max(4, players)
    bots = [w.add_player(f"b{i + 1}", f"Bot{i + 1}") for i in range(n)]
    w.phase = "night"
    w.day = day
    report = SimReport(ok=True)
    try:
        for m in live:                                   # live mechanics are already patched
            w.load_mechanic(m.model_copy(update={"patches": []}))
        cand = candidate.model_copy(update={"creator": bots[0].id, "day": day})
        w.load_mechanic(cand)
        w.start_day(day, seconds)
    except Exception as e:  # noqa: BLE001
        return SimReport(ok=False, errors=[f"loading crashed: {type(e).__name__}: {e}"])

    ticks = int(seconds / DT)
    t0 = time.perf_counter()
    next_think = 0.0
    goals: dict[str, tuple[float, float]] = {}
    try:
        for _ in range(ticks):
            if w.clock >= next_think:
                next_think = w.clock + THINK_S
                for b in bots:
                    _bot(w, b, rng, goals)
            for b in bots:
                b.input_queue.append((b.input_dx, b.input_dy, b.input_seq + 1))
            w.tick(DT)
            w.take_outbox()
            if len(w.entities) > max_entities:
                report.errors.append(f"entity count exceeded {max_entities}")
                break
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"engine crashed: {type(e).__name__}: {e}")
    elapsed = time.perf_counter() - t0
    report.avg_tick_ms = elapsed / max(1, w.tick_no) * 1000

    prefix = f"{cand.id}/"
    for key, count in w.errors.items():
        if key.startswith(prefix):
            report.errors.append(f"rule '{key[len(prefix):]}' raised errors at runtime ({count}x)")
    cap = min(points_cap, cand.limits.points_per_player_per_day)
    for b in bots:
        got = w.ledger.gained[(cand.id, b.id, day)]
        if got > cap + 1e-6:
            report.errors.append(f"{b.name} gained {got} points from this mechanic (cap {cap})")
    if report.avg_tick_ms > MAX_AVG_TICK_MS:
        report.errors.append(f"too slow: {report.avg_tick_ms:.1f} ms per tick (limit {MAX_AVG_TICK_MS})")
    report.fired = w.fired[cand.id]
    if report.fired == 0 and not cand.patches:
        report.warnings.append("none of its rules or abilities fired during the simulation")
    if w.queue.dropped:
        report.warnings.append(f"{w.queue.dropped} events were dropped (chains too deep or too many per tick)")
    report.ok = not report.errors
    return report


def _bot(w: World, b, rng: random.Random, memory: dict) -> None:
    """Walk toward something interesting, poke it, and spam abilities/items/prompts."""
    if not b.alive:
        return
    goals = [(e.x, e.y) for e in w.entities.values() if not e.dead]
    goals += [(z.x, z.y) for z in w.zones.values()]
    goals += [(p.x, p.y) for p in w.players.values() if p is not b]
    if not goals or rng.random() < 0.2:
        goals.append((rng.uniform(1, w.map_size - 1), rng.uniform(1, w.map_size - 1)))
    if b.id not in memory or rng.random() < 0.25:
        memory[b.id] = rng.choice(goals)
    gx, gy = memory[b.id]
    d = math.hypot(gx - b.x, gy - b.y)
    b.input_dx, b.input_dy = ((gx - b.x) / d, (gy - b.y) / d) if d > 0.5 else (0.0, 0.0)
    w.queue_action("interact", b.id)
    for aid in list(w.abilities):
        if w.player_has_ability(b, aid) and rng.random() < 0.3:
            target = rng.choice([*w.entities, *[p for p in w.players if p != b.id]] or [None])
            point = {"x": rng.uniform(0, w.map_size), "y": rng.uniform(0, w.map_size)}
            w.queue_action("use_ability", b.id, aid, target, point, rng.uniform(-180, 180))
    for item, n in list(b.items.items()):
        if n > 0 and item in w.items and w.items[item].usable and rng.random() < 0.3:
            w.queue_action("use_item", b.id, item)
    for qid, pr in list(w.prompts.items()):
        if pr["player"] == b.id and rng.random() < 0.7:
            w.queue_action("answer_prompt", b.id, qid, rng.randrange(len(pr["options"])))
    for vid, v in list(w.votes.items()):
        if b.id in v["voters"] and b.id not in v["choices"]:
            w.queue_action("answer_prompt", b.id, vid, rng.randrange(len(v["options"])))
    if rng.random() < 0.02:
        w.queue_action("chat", b.id, rng.choice(["hello", "gg", "nice", "help"]))
