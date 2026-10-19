"""Event queue + rule dispatch (SPEC §4.4)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from game.dsl import SELF_PACED, Mechanic, Rule

from .exprs import Ctx, truthy, evaluate
from .state import Entity, Player

if TYPE_CHECKING:
    from .world import World

MAX_DEPTH = 8
MAX_EVENTS_PER_TICK = 500


@dataclass
class Event:
    kind: str
    actor: Any = None
    target: Any = None
    data: dict = field(default_factory=dict)
    depth: int = 0
    only: str | None = None           # deliver to a single rule key (every / near / idle)


class EventQueue:
    def __init__(self, world: "World") -> None:
        self.world = world
        self.q: deque[Event] = deque()
        self.processed = 0
        self.dropped = 0

    def push(self, ev: Event) -> None:
        if ev.depth > MAX_DEPTH:
            self.dropped += 1
            self.world.log_once(f"depth:{ev.kind}", f"event chain too deep, dropped '{ev.kind}'")
            return
        self.q.append(ev)

    def drain(self) -> None:
        while self.q:
            if self.processed >= MAX_EVENTS_PER_TICK:
                self.dropped += len(self.q)
                self.world.log_once("events:overflow", f"over {MAX_EVENTS_PER_TICK} events in one tick; dropping")
                self.q.clear()
                return
            self.processed += 1
            dispatch(self.world, self.q.popleft())

    def new_tick(self) -> None:
        self.processed = 0


def rule_key(m: Mechanic, r: Rule) -> str:
    return f"{m.id}/{r.id}"


def matches(world: "World", m: Mechanic, r: Rule, ev: Event) -> bool:
    t = r.trigger
    d = ev.data
    tgt = ev.target
    if t.entity is not None and not (isinstance(tgt, Entity) and tgt.type == t.entity):
        return False
    if t.tag is not None:
        et = world.entity_types.get(tgt.type) if isinstance(tgt, Entity) else None
        if et is None or t.tag not in et.tags:
            return False
    if t.zone is not None and d.get("zone") != t.zone:
        return False
    if t.ability is not None and d.get("ability") != t.ability:
        return False
    if t.item is not None and d.get("item") != t.item:
        return False
    if t.status is not None and d.get("status") != t.status:
        return False
    if t.property is not None and d.get("property") != t.property:
        return False
    if t.on == "custom" and d.get("event") != t.event:
        return False
    if t.chat_contains is not None and t.chat_contains.lower() not in str(d.get("text", "")).lower():
        return False
    if t.actor != "anyone":
        aid = getattr(ev.actor, "id", None)
        if t.actor == "creator" and aid != m.creator:
            return False
        if t.actor == "not_creator" and aid == m.creator:
            return False
        owner = getattr(tgt, "owner", None)
        if t.actor == "owner" and (owner is None or owner != aid):
            return False
        if t.actor == "not_owner" and owner is not None and owner == aid:
            return False
    return True


def exclusive_blocked(world: "World", m: Mechanic, actor: Any) -> bool:
    """Creator exclusivity window on the mechanic's first live day."""
    ex = m.creator_perk.exclusive_s
    if ex <= 0 or world.day != m.day or world.time >= ex:
        return False
    return isinstance(actor, Player) and actor.id != m.creator


def dispatch(world: "World", ev: Event) -> int:
    """Run every matching rule for this event. Returns the number of rules whose `do` ran."""
    fired = 0
    for m, r in list(world.rule_index.get(ev.kind, ())):
        key = rule_key(m, r)
        if ev.only is not None and ev.only != key:
            continue
        if m.disabled or r.disabled or key in world.disabled_rules:
            continue
        if not matches(world, m, r, ev):
            continue
        if r.trigger.on not in SELF_PACED and exclusive_blocked(world, m, ev.actor):
            continue
        pid = ev.actor.id if isinstance(ev.actor, Player) else None
        cd_mult = ev.actor.stats.get("cooldown_mult", 1.0) if isinstance(ev.actor, Player) else 1.0
        if not world.limiter.check(key, r.limits, pid, world.clock, cd_mult):
            continue
        ctx = Ctx(mechanic=m, actor=ev.actor, target=ev.target, event=dict(ev.data), depth=ev.depth)
        try:
            ok = all(truthy(evaluate(world, c, ctx)) for c in r.if_)
            if ok:
                world.limiter.consume(key, pid, world.clock)
                fired += 1
                world.fired[m.id] += 1
                world.run(r.do, ctx)
            elif r.else_:
                world.run(r.else_, ctx)
        except Exception as e:  # noqa: BLE001 — a rule must never crash the room
            world.rule_error(key, e)
    return fired
