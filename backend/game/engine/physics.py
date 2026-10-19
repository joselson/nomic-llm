"""Movement, collision, contacts (collide/collect/near/zones/idle/touch transfers)."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import TYPE_CHECKING, Iterable

from .events import Event, rule_key
from .state import INPUT_DT, Entity, Player

if TYPE_CHECKING:
    from .world import World

CONTACT_EPS = 0.08
MAX_ENTITY_RADIUS = 4.0


class SpatialHash:
    def __init__(self, cell: float = 2.0) -> None:
        self.cell = cell
        self.buckets: dict[tuple[int, int], list] = defaultdict(list)

    def build(self, objs: Iterable) -> "SpatialHash":
        self.buckets.clear()
        c = self.cell
        for o in objs:
            self.buckets[(int(o.x // c), int(o.y // c))].append(o)
        return self

    def query(self, x: float, y: float, r: float):
        c = self.cell
        for cx in range(int((x - r) // c), int((x + r) // c) + 1):
            for cy in range(int((y - r) // c), int((y + r) // c) + 1):
                yield from self.buckets.get((cx, cy), ())


def touching(a, b) -> bool:
    if isinstance(b, Entity) and b.square:
        return _aabb_pen(a.x, a.y, a.radius + CONTACT_EPS, b.x, b.y, b.radius) is not None
    if isinstance(a, Entity) and a.square:
        return _aabb_pen(b.x, b.y, b.radius + CONTACT_EPS, a.x, a.y, a.radius) is not None
    return math.hypot(a.x - b.x, a.y - b.y) < a.radius + b.radius + CONTACT_EPS


def _aabb_pen(cx, cy, r, bx, by, half):
    """Push vector to move circle (cx,cy,r) out of the square centered at (bx,by)."""
    qx, qy = max(bx - half, min(cx, bx + half)), max(by - half, min(cy, by + half))
    dx, dy = cx - qx, cy - qy
    d = math.hypot(dx, dy)
    if d >= r:
        return None
    if d > 1e-9:
        return (dx / d * (r - d), dy / d * (r - d))
    # center inside the box: push out along the shallowest axis
    opts = [(bx - half - r - cx, 0.0), (bx + half + r - cx, 0.0),
            (0.0, by - half - r - cy), (0.0, by + half + r - cy)]
    return min(opts, key=lambda v: abs(v[0]) + abs(v[1]))


def _circle_pen(ax, ay, ar, bx, by, br, rng):
    dx, dy = ax - bx, ay - by
    d = math.hypot(dx, dy)
    overlap = ar + br - d
    if overlap <= 0:
        return None
    if d < 1e-9:
        a = rng.uniform(0, math.tau)
        return (math.cos(a) * overlap, math.sin(a) * overlap)
    return (dx / d * overlap, dy / d * overlap)


def terrain_mult(world: "World", x: float, y: float) -> float:
    mult = 1.0
    t = world.tiles.at(x, y)
    if t is not None and t in world.tile_defs:
        mult *= world.tile_defs[t].speed_mult
    for z in world.zones.values():
        zd = world.zone_defs.get(z.zone)
        if zd is not None and zd.speed_mult != 1.0 and z.contains(x, y):
            mult *= zd.speed_mult
    return mult


def zone_blocks(world: "World", z, p: Player) -> bool:
    zd = world.zone_defs.get(z.zone)
    if zd is None or zd.blocks == "nobody":
        return False
    if zd.blocks == "everyone":
        return True
    if zd.blocks == "others":
        m = world.mechanics.get(z.mechanic_id)
        return m is None or m.creator != p.id
    return z.owner != p.id      # non_owner


def move_body(world: "World", o, dx: float, dy: float) -> float:
    """Move with collision resolution; returns distance actually travelled."""
    x0, y0 = o.x, o.y
    o.x, o.y = o.x + dx, o.y + dy
    ghost = "ghost" in o.flags
    r = o.radius
    projectile = isinstance(o, Entity) and o.brain.get("projectile")
    for _ in range(2):
        if not ghost and not projectile:
            for s in world.solid_hash.query(o.x, o.y, r + MAX_ENTITY_RADIUS):
                if s is o or s.dead:
                    continue
                pen = (_aabb_pen(o.x, o.y, r, s.x, s.y, s.radius) if s.square
                       else _circle_pen(o.x, o.y, r, s.x, s.y, s.radius, world.rng))
                if pen:
                    o.x += pen[0]
                    o.y += pen[1]
            if isinstance(o, Player):
                for q in world.players.values():
                    if q is o or not q.alive or not q.connected or "ghost" in q.flags:
                        continue
                    pen = _circle_pen(o.x, o.y, r, q.x, q.y, q.radius, world.rng)
                    if pen:
                        o.x += pen[0]
                        o.y += pen[1]
            # unwalkable tiles
            if world.blocking_tiles:
                for tx in range(int(math.floor(o.x - r)), int(math.floor(o.x + r)) + 1):
                    for ty in range(int(math.floor(o.y - r)), int(math.floor(o.y + r)) + 1):
                        t = world.tiles.cells.get((tx, ty))
                        if t and t[0] in world.blocking_tiles:
                            pen = _aabb_pen(o.x, o.y, r, tx + 0.5, ty + 0.5, 0.5)
                            if pen:
                                o.x += pen[0]
                                o.y += pen[1]
            if isinstance(o, Player):
                for z in world.zones.values():
                    if zone_blocks(world, z, o) and z.contains(o.x, o.y) and not z.contains(x0, y0):
                        o.x, o.y = x0, y0
        m = world.map_size
        o.x = min(max(o.x, r), m - r)
        o.y = min(max(o.y, r), m - r)
    return math.hypot(o.x - x0, o.y - y0)


def step_motion(world: "World", o, dt: float) -> None:
    mo = o.motion
    speed = math.hypot(mo.vx, mo.vy)
    step = min(mo.remaining, speed * dt)
    if speed <= 0 or step <= 0:
        o.motion = None
        return
    moved = move_body(world, o, mo.vx / speed * step, mo.vy / speed * step)
    mo.remaining -= step
    if mo.remaining <= 1e-3 or moved < step * 0.3:
        if mo.goal is not None and moved >= step * 0.3:
            o.x, o.y = mo.goal
        o.motion = None


def step_players(world: "World", dt: float) -> None:
    """Players move by consuming queued input commands (INPUT_DT each), limited by a
    real-time budget so clients can predict exactly and can't speed-hack."""
    for p in world.players.values():
        p.input_budget = min(p.input_budget + dt, 4 * INPUT_DT)
        if not p.alive or not p.connected:
            p.input_queue.clear()
            continue
        if p.motion is not None:
            while p.input_queue and p.input_budget >= INPUT_DT - 1e-9:
                p.input_seq = p.input_queue.popleft()[2]
                p.input_budget -= INPUT_DT
            step_motion(world, p, dt)
            p.last_active = world.clock
            continue
        while p.input_queue and p.input_budget >= INPUT_DT - 1e-9:
            dx, dy, seq = p.input_queue.popleft()
            p.input_budget -= INPUT_DT
            p.input_seq = seq
            p.input_dx, p.input_dy = dx, dy
            if "frozen" in p.flags:
                continue
            if "reversed_controls" in p.flags:
                dx, dy = -dx, -dy
            n = math.hypot(dx, dy)
            if n < 1e-3:
                continue
            if n > 1:
                dx, dy = dx / n, dy / n
            speed = max(0.0, p.stats["speed"]) * terrain_mult(world, p.x, p.y)
            move_body(world, p, dx * speed * INPUT_DT, dy * speed * INPUT_DT)
            p.facing = math.degrees(math.atan2(dy, dx))
            p.last_active = world.clock


# --------------------------------------------------------------------------- #
# Contacts
# --------------------------------------------------------------------------- #
def detect_contacts(world: "World") -> None:
    now = world.clock
    players = [p for p in world.players.values() if p.alive and p.connected]

    # player <-> player
    pairs: set[tuple[str, str]] = set()
    for i, a in enumerate(players):
        for b in players[i + 1:]:
            if touching(a, b):
                pairs.add((a.id, b.id))
    for a_id, b_id in pairs - world.pp_contacts:
        a, b = world.players[a_id], world.players[b_id]
        world.push(Event("collide", a, b, {}))
        world.push(Event("collide", b, a, {}))
    world.pp_contacts = pairs
    for a_id, b_id in pairs:
        a, b = world.players[a_id], world.players[b_id]
        if not _transfer(world, a, b, now):
            _transfer(world, b, a, now)

    # player <-> entity
    ents = world.entity_hash
    current: set[tuple[str, str]] = set()
    for p in players:
        for e in list(ents.query(p.x, p.y, p.radius + MAX_ENTITY_RADIUS)):
            if e.dead or not touching(p, e):
                continue
            et = world.entity_types.get(e.type)
            if et is None:
                continue
            if et.collectible:
                world.push(Event("collect", p, e, {"entity": e.type}))
                world.destroy_entity(e, p)
                continue
            if e.brain.get("projectile"):
                if e.owner == p.id:
                    continue
                world.push(Event("collide", p, e, {"entity": e.type}))
                world.destroy_entity(e, p)
                continue
            key = (p.id, e.id)
            current.add(key)
            if key not in world.pe_contacts:
                world.push(Event("collide", p, e, {"entity": e.type}))
    world.pe_contacts = current

    # zones
    for p in players:
        prev = world.zone_membership.get(p.id, {})
        inside = {z.id: z.zone for z in world.zones.values() if z.contains(p.x, p.y)}
        for zid, zone in inside.items():
            if zid not in prev:
                world.push(Event("enter_zone", p, world.zones.get(zid), {"zone": zone}))
        for zid, zone in prev.items():
            if zid not in inside:
                world.push(Event("leave_zone", p, world.zones.get(zid), {"zone": zone}))
        world.zone_membership[p.id] = inside

    # near (edge-triggered per rule)
    near_now: set[tuple[str, str, str]] = set()
    for m, r in world.rule_index.get("near", ()):
        key = rule_key(m, r)
        rad = r.trigger.radius or 2.0
        if r.trigger.entity or r.trigger.tag:
            cands = []
            for p in players:
                for e in ents.query(p.x, p.y, rad + MAX_ENTITY_RADIUS):
                    if e.dead:
                        continue
                    if r.trigger.entity and e.type != r.trigger.entity:
                        continue
                    if r.trigger.tag and r.trigger.tag not in world.entity_types[e.type].tags:
                        continue
                    cands.append((p, e))
        else:
            cands = [(p, q) for p in players for q in players if q is not p]
        for p, o in cands:
            if math.hypot(p.x - o.x, p.y - o.y) - getattr(o, "radius", 0) <= rad:
                k = (key, p.id, o.id)
                near_now.add(k)
                if k not in world.near_state:
                    world.push(Event("near", p, o, {"entity": getattr(o, "type", None)}, only=key))
    world.near_state = near_now

    # idle
    for m, r in world.rule_index.get("idle", ()):
        key = rule_key(m, r)
        for p in players:
            k = (key, p.id)
            if now - p.last_active >= (r.trigger.idle_s or 10):
                if k not in world.idle_state:
                    world.idle_state.add(k)
                    world.push(Event("idle", p, None, {"idle_s": r.trigger.idle_s}, only=key))
            else:
                world.idle_state.discard(k)


def _transfer(world: "World", a: Player, b: Player, now: float) -> bool:
    moved = False
    for inst in list(a.statuses):
        sd = world.statuses.get(inst.status)
        if sd is None or not sd.transfers_on_touch or now < inst.transferable_at:
            continue
        if any(s.status == inst.status for s in b.statuses) or "immune" in b.flags:
            continue
        a.statuses.remove(inst)
        inst.transferable_at = now + sd.transfer_cooldown_s
        inst.source = a.id
        b.statuses.append(inst)
        world.recompute_stats(a)
        world.recompute_stats(b)
        world.push(Event("status_transferred", a, b, {"status": inst.status, "from": a, "to": b}))
        moved = True
    return moved
