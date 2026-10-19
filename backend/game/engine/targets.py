"""Target, selector and position resolution (SPEC §4.6)."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from game.dsl import MAX_SELECTION, Around, Point, Ref, Selector

from .exprs import Ctx, as_obj, evaluate, num, truthy
from .state import Entity, Player, WorldProxy, ZoneInstance, pos_of

if TYPE_CHECKING:
    from .world import World

MULTI_NAMES = {"everyone", "others"}


def resolve_target(world: "World", target: Any, ctx: Ctx) -> tuple[list[Any], bool]:
    """Return (objects, is_multi). is_multi → effects bind `it` per element."""
    if target is None:
        return [], False
    if isinstance(target, Selector):
        return select(world, target, ctx), True
    if isinstance(target, Ref):
        v = evaluate(world, target, ctx)
        if isinstance(v, (list, tuple)):
            return [o for o in (as_obj(world, x) for x in v) if o is not None], True
        v = as_obj(world, v)
        return ([v] if v is not None and not isinstance(v, (int, float, bool, str, dict)) else []), False
    name = target
    players = world.active_players()
    if name in ("actor", "target", "it"):
        v = as_obj(world, getattr(ctx, name))
        return ([v] if v is not None else []), False
    if name == "creator":
        p = world.players.get(ctx.mechanic.creator) if ctx.mechanic else None
        return ([p] if p else []), False
    if name == "everyone":
        return players, True
    if name == "others":
        return [p for p in players if p is not ctx.actor], True
    if name in ("leader", "last_place"):
        if not players:
            return [], False
        best = max(p.points for p in players) if name == "leader" else min(p.points for p in players)
        return [world.rng.choice([p for p in players if p.points == best])], False
    if name == "random_player":
        return ([world.rng.choice(players)] if players else []), False
    if name in ("world", "mechanic"):
        return [world.proxy], False
    return [], False


def resolve_one(world: "World", target: Any, ctx: Ctx) -> Any:
    objs, _ = resolve_target(world, target, ctx)
    return objs[0] if objs else None


def select(world: "World", sel: Selector, ctx: Ctx) -> list[Any]:
    if sel.kind == "players":
        cands: list[Any] = list(world.active_players())
    elif sel.kind == "zones":
        cands = list(world.zones.values())
        if sel.types:
            cands = [z for z in cands if z.zone in sel.types]
    else:
        cands = [e for e in world.entities.values() if not e.dead]
        if sel.types:
            cands = [e for e in cands if e.type in sel.types]
        if sel.tags:
            tagset = set(sel.tags)
            cands = [e for e in cands
                     if tagset & set(getattr(world.entity_types.get(e.type), "tags", []))]
    if sel.owner is not None:
        owner = resolve_one(world, sel.owner, ctx)
        oid = getattr(owner, "id", None)
        cands = [c for c in cands if getattr(c, "owner", None) == oid]
    if sel.exclude_actor and ctx.actor is not None:
        cands = [c for c in cands if c is not ctx.actor]
    if sel.near is not None:
        center = resolve_position(world, sel.near, ctx)
        r = num(evaluate(world, sel.radius, ctx)) if sel.radius is not None else 3.0
        if center is not None:
            cands = [c for c in cands if math.hypot(c.x - center[0], c.y - center[1]) <= r]
    if sel.where is not None:
        cands = [c for c in cands if truthy(evaluate(world, sel.where, ctx.with_(it=c)))]
    if sel.sort_by is not None:
        keyed = [(num(evaluate(world, sel.sort_by, ctx.with_(it=c))), i, c) for i, c in enumerate(cands)]
        keyed.sort(key=lambda t: (t[0], t[1]), reverse=sel.descending)
        cands = [c for _, _, c in keyed]
    elif sel.kind == "players" or sel.limit < len(cands):
        # stable but unbiased order when the selection is truncated
        world.rng.shuffle(cands)
    return cands[: min(sel.limit, MAX_SELECTION)]


def clamp_pos(world: "World", x: float, y: float) -> tuple[float, float]:
    m = world.map_size
    return (min(max(x, 0.5), m - 0.5), min(max(y, 0.5), m - 0.5))


def resolve_position(world: "World", pos: Any, ctx: Ctx) -> tuple[float, float] | None:
    if pos is None:
        return None
    if isinstance(pos, Point):
        return clamp_pos(world, num(evaluate(world, pos.x, ctx)), num(evaluate(world, pos.y, ctx)))
    if isinstance(pos, Around):
        base = resolve_one(world, pos.around, ctx)
        p = pos_of(base)
        if p is None:
            return None
        r = num(evaluate(world, pos.radius, ctx))
        x, y = p
        if r > 0:
            ang, d = world.rng.uniform(0, math.tau), r * math.sqrt(world.rng.random())
            x, y = x + math.cos(ang) * d, y + math.sin(ang) * d
        if pos.offset is not None:
            x += num(evaluate(world, pos.offset.x, ctx))
            y += num(evaluate(world, pos.offset.y, ctx))
        return clamp_pos(world, x, y)
    m = world.map_size
    if pos in ("actor", "target", "it"):
        p = pos_of(as_obj(world, getattr(ctx, pos)))
        return clamp_pos(world, *p) if p else None
    if pos == "creator":
        p = pos_of(world.players.get(ctx.mechanic.creator) if ctx.mechanic else None)
        return clamp_pos(world, *p) if p else None
    if pos == "event":
        p = pos_of(ctx.event.get("point")) or pos_of(ctx.event)
        if p is None:
            p = pos_of(as_obj(world, ctx.event.get("target"))) or pos_of(ctx.target) or pos_of(ctx.actor)
        return clamp_pos(world, *p) if p else None
    if pos == "random":
        return clamp_pos(world, world.rng.uniform(0, m), world.rng.uniform(0, m))
    if pos == "random_empty":
        return world.random_empty()
    if pos == "center":
        return (m / 2, m / 2)
    if pos == "edge":
        t = world.rng.uniform(0, m)
        side = world.rng.randrange(4)
        return clamp_pos(world, *[(t, 0), (t, m), (0, t), (m, t)][side])
    return None


def is_object(v: Any) -> bool:
    return isinstance(v, (Player, Entity, ZoneInstance, WorldProxy))
