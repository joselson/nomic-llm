"""Entity movement AI (SPEC §4.9)."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .exprs import Ctx, evaluate, num
from .physics import move_body, step_motion
from .state import Entity, Player, pos_of
from .targets import resolve_one, resolve_position

if TYPE_CHECKING:
    from .world import World

THINK_S = 1.0


def _nearest_player(world: "World", e: Entity, within: float | None, around=None):
    cx, cy = around if around else (e.x, e.y)
    best, bd = None, math.inf
    for p in world.active_players():
        d = math.hypot(p.x - cx, p.y - cy)
        if (within is None or d <= within) and d < bd:
            best, bd = p, d
    return best


def _think(world: "World", e: Entity, now: float) -> None:
    et = world.entity_types[e.type]
    b = et.behavior
    br = e.brain
    owner = world.players.get(e.owner) if e.owner else None
    ctx = Ctx(mechanic=world.mechanics.get(e.mechanic_id), actor=owner, it=e, target=None)
    br["speed"] = max(0.0, min(20.0, num(evaluate(world, b.speed, ctx))))
    br["range"] = num(evaluate(world, b.range, ctx)) if b.range is not None else None
    tgt = resolve_one(world, b.target, ctx) if b.target is not None else None
    br["target"] = tgt if tgt is not e else None
    if b.kind == "move_to" and "goal" not in br:
        br["goal"] = resolve_position(world, b.stop_at, ctx) if b.stop_at is not None else None
    br["next_think"] = now + THINK_S


def _walk(world: "World", e: Entity, tx: float, ty: float, speed: float, dt: float, stop: float = 0.05) -> bool:
    dx, dy = tx - e.x, ty - e.y
    d = math.hypot(dx, dy)
    if d <= stop:
        return True
    step = min(d - stop, speed * dt)
    e.facing = math.degrees(math.atan2(dy, dx))
    move_body(world, e, dx / d * step, dy / d * step)
    return False


def step_behaviors(world: "World", dt: float) -> None:
    now = world.clock
    m = world.map_size
    for e in list(world.entities.values()):
        if e.dead:
            continue
        et = world.entity_types.get(e.type)
        if et is None:
            continue
        if e.motion is not None:
            step_motion(world, e, dt)
            continue
        b = et.behavior
        if b.kind == "static" or "frozen" in e.flags:
            continue
        br = e.brain
        if now >= br.get("next_think", 0):
            _think(world, e, now)
        speed = br.get("speed", 1.0) * e.stats.get("speed", 1.0)
        rng_ = br.get("range")
        tgt = br.get("target")
        if isinstance(tgt, Entity) and tgt.dead or isinstance(tgt, Player) and not tgt.alive:
            tgt = None
        kind = b.kind

        if kind == "wander":
            if now >= br.get("turn_at", 0):
                br["heading"] = world.rng.uniform(0, math.tau)
                br["turn_at"] = now + world.rng.uniform(2, 4)
            h = br["heading"]
            nx, ny = e.x + math.cos(h) * speed * dt, e.y + math.sin(h) * speed * dt
            if not (e.radius <= nx <= m - e.radius) or not (e.radius <= ny <= m - e.radius):
                br["heading"] = h + math.pi
            e.facing = math.degrees(br["heading"])
            move_body(world, e, math.cos(h) * speed * dt, math.sin(h) * speed * dt)

        elif kind in ("follow", "chase", "flee"):
            if tgt is None and b.target is None:
                tgt = _nearest_player(world, e, rng_ if rng_ else (None if kind != "flee" else 5.0))
            p = pos_of(tgt)
            if p is None:
                continue
            d = math.hypot(p[0] - e.x, p[1] - e.y)
            if rng_ is not None and d > rng_ and kind != "follow":
                continue
            if kind == "flee":
                if d < 1e-6:
                    continue
                if rng_ is None and d > 5.0:
                    continue
                _walk(world, e, e.x - (p[0] - e.x) / d * 2, e.y - (p[1] - e.y) / d * 2, speed, dt)
            else:
                _walk(world, e, p[0], p[1], speed, dt, stop=1.5 if kind == "follow" else 0.05)

        elif kind == "patrol":
            path = b.path or []
            if not path:
                continue
            i = br.get("wp", 0) % len(path)
            ctx = Ctx(mechanic=world.mechanics.get(e.mechanic_id), it=e)
            tx, ty = num(evaluate(world, path[i].x, ctx)), num(evaluate(world, path[i].y, ctx))
            if _walk(world, e, tx, ty, speed, dt, stop=0.2):
                br["wp"] = i + 1

        elif kind == "orbit":
            c = pos_of(tgt) if tgt is not None else (e.spawn_x, e.spawn_y)
            if c is None:
                continue
            radius = max(0.5, rng_ if rng_ is not None else 3.0)
            ang = br.get("angle")
            if ang is None:
                ang = math.atan2(e.y - c[1], e.x - c[0])
            ang += speed / radius * dt
            br["angle"] = ang
            e.x = min(max(c[0] + math.cos(ang) * radius, e.radius), m - e.radius)
            e.y = min(max(c[1] + math.sin(ang) * radius, e.radius), m - e.radius)

        elif kind in ("projectile", "bounce"):
            h = br.get("heading")
            if h is None:
                h = br["heading"] = math.radians(e.facing)
            dx, dy = math.cos(h) * speed * dt, math.sin(h) * speed * dt
            nx, ny = e.x + dx, e.y + dy
            out_x = not (e.radius <= nx <= m - e.radius)
            out_y = not (e.radius <= ny <= m - e.radius)
            if kind == "projectile":
                if out_x or out_y or _hits_solid(world, e, nx, ny):
                    world.destroy_entity(e, None)
                    continue
                e.x, e.y = nx, ny
            else:
                if out_x:
                    h = math.pi - h
                if out_y:
                    h = -h
                br["heading"] = h
                if not (out_x or out_y):
                    before = (e.x, e.y)
                    moved = move_body(world, e, dx, dy)
                    if moved < speed * dt * 0.5:
                        # bounced off a solid: reflect along the push direction
                        px, py = e.x - before[0] - dx, e.y - before[1] - dy
                        n = math.hypot(px, py)
                        if n > 1e-9:
                            nx_, ny_ = px / n, py / n
                            vx, vy = math.cos(h), math.sin(h)
                            dot = vx * nx_ + vy * ny_
                            br["heading"] = math.atan2(vy - 2 * dot * ny_, vx - 2 * dot * nx_)
            e.facing = math.degrees(br["heading"])

        elif kind == "guard":
            radius = rng_ if rng_ is not None else 4.0
            intruder = tgt if tgt is not None and pos_of(tgt) and math.hypot(
                tgt.x - e.spawn_x, tgt.y - e.spawn_y) <= radius else \
                _nearest_player(world, e, radius, around=(e.spawn_x, e.spawn_y))
            if intruder is not None:
                _walk(world, e, intruder.x, intruder.y, speed, dt)
            else:
                _walk(world, e, e.spawn_x, e.spawn_y, speed, dt, stop=0.2)

        elif kind == "move_to":
            goal = br.get("goal")
            if goal:
                if _walk(world, e, goal[0], goal[1], speed, dt, stop=0.1):
                    br["goal"] = None


def _hits_solid(world: "World", e: Entity, x: float, y: float) -> bool:
    for s in world.solid_hash.query(x, y, e.radius + 4):
        if s is e or s.dead:
            continue
        if math.hypot(s.x - x, s.y - y) < s.radius + e.radius:
            return True
    return False
