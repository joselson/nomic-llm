"""The engine: loads mechanics, owns all state, runs the 20 Hz tick (SPEC §4)."""
from __future__ import annotations

import heapq
import itertools
import logging
import math
import random
from collections import Counter
from typing import Any, Callable

from game.dsl import (POINTS_CAP_PER_MECHANIC, AbilityDef, ChoiceOption, EntityType,
                      ItemDef, Mechanic, PropertyDef, Rule, StatusDef, TileDef, ZoneDef, apply_patch)

from . import physics
from .behaviors import step_behaviors
from .effects import run_effects
from .events import Event, EventQueue, dispatch, exclusive_blocked, rule_key
from .exprs import Ctx, evaluate, get_attr, num, tidy, to_text, truthy
from .limits import Ledger, Limiter
from .state import (BASE_STATS, PALETTE, RESPAWN_S, Entity, Player, StatusInstance,
                    TileMap, WorldProxy, ZoneInstance)
from .targets import clamp_pos, resolve_position

log = logging.getLogger("engine")

MAX_ZONES_PER_DEF = 20
MAX_PROMPTS_PER_PLAYER = 3
MAX_ERRORS = 3


class World:
    def __init__(self, *, map_size: int = 48, seed: int | None = None,
                 points_cap: float = POINTS_CAP_PER_MECHANIC, max_entities: int = 1000,
                 win_points: float = 100) -> None:
        self.map_size = map_size
        self.rng = random.Random(seed)
        self.points_cap = points_cap
        self.max_entities = max_entities
        self.win_points = win_points

        self.players: dict[str, Player] = {}
        self.entities: dict[str, Entity] = {}
        self.zones: dict[str, ZoneInstance] = {}
        self.tiles = TileMap()
        self.world_props: dict[str, Any] = {}
        self.proxy = WorldProxy(self)

        self.mechanics: dict[str, Mechanic] = {}
        self.items: dict[str, ItemDef] = {}
        self.entity_types: dict[str, EntityType] = {}
        self.statuses: dict[str, StatusDef] = {}
        self.abilities: dict[str, AbilityDef] = {}
        self.zone_defs: dict[str, ZoneDef] = {}
        self.tile_defs: dict[str, TileDef] = {}
        self.prop_defs: dict[str, PropertyDef] = {}
        self.player_prop_defs: dict[str, PropertyDef] = {}
        self.owner_of: dict[tuple[str, str], str] = {}     # (kind, id) -> mechanic id
        self.rule_index: dict[str, list[tuple[Mechanic, Rule]]] = {}
        self.interactable: set[str] | None = set()          # None = every entity type
        self.blocking_tiles: set[str] = set()

        self.limiter = Limiter()
        self.ledger = Ledger(self)
        self.queue = EventQueue(self)
        self.timers: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()
        self._ids = itertools.count(1)

        self.clock = 0.0           # seconds of play since game start (frozen at night)
        self.day = 0
        self.time = 0.0            # seconds since day start
        self.play_seconds = 420.0
        self.phase = "play"
        self.tick_no = 0
        self.scoring_enabled = True
        self.effects_this_tick = 0
        self._every_gen = 0

        self.outbox: list[tuple[list[str] | None, dict]] = []
        self.actions: list[tuple[str, tuple]] = []
        self.logs: list[str] = []
        self._logged: set[str] = set()
        self.errors: Counter[str] = Counter()
        self.disabled_rules: set[str] = set()
        self.incidents: list[str] = []
        self.fired: Counter[str] = Counter()
        self.winner_ids: list[str] = []

        self.pp_contacts: set[tuple[str, str]] = set()
        self.pe_contacts: set[tuple[str, str]] = set()
        self.zone_membership: dict[str, dict[str, str]] = {}
        self.near_state: set[tuple[str, str, str]] = set()
        self.idle_state: set[tuple[str, str]] = set()
        self.prompts: dict[str, dict] = {}
        self.votes: dict[str, dict] = {}
        self.solid_hash = physics.SpatialHash()
        self.entity_hash = physics.SpatialHash()
        self.type_counts: Counter[str] = Counter()

    # ------------------------------------------------------------------ #
    # Logging / output
    # ------------------------------------------------------------------ #
    def log(self, msg: str) -> None:
        self.logs.append(msg)
        if len(self.logs) > 500:
            del self.logs[:100]
        log.info(msg)

    def log_once(self, key: str, msg: str) -> None:
        if key not in self._logged:
            self._logged.add(key)
            self.log(msg)

    def send(self, pids: list[str] | None, msg: dict) -> None:
        self.outbox.append((pids, msg))

    def take_outbox(self) -> list[tuple[list[str] | None, dict]]:
        out, self.outbox = self.outbox, []
        return out

    def rule_error(self, key: str, exc: BaseException) -> None:
        self.errors[key] += 1
        self.log(f"rule {key} error #{self.errors[key]}: {type(exc).__name__}: {exc}")
        if self.errors[key] >= MAX_ERRORS and key not in self.disabled_rules:
            self.disabled_rules.add(key)
            note = f"'{key}' was disabled after {MAX_ERRORS} runtime errors"
            self.incidents.append(note)
            self.send(None, {"t": "message", "text": f"⚠️ {note}", "style": "log", "from": None})

    # ------------------------------------------------------------------ #
    # Mechanics
    # ------------------------------------------------------------------ #
    def load_mechanic(self, m: Mechanic) -> Mechanic:
        for p in m.patches:
            target = self.mechanics.get(p.mechanic)
            if target is None:
                self.log(f"patch skipped: unknown mechanic '{p.mechanic}'")
                continue
            try:
                self.mechanics[p.mechanic] = apply_patch(target, p)
            except Exception as e:  # noqa: BLE001
                self.log(f"patch {p.op} {p.mechanic}{p.path} failed: {e}")
        self.mechanics[m.id] = m
        self._reindex()

        for pd in m.player_props:
            for pl in self.players.values():
                pl.props.setdefault(pd.name, pd.default)
        for pd in m.world_props:
            self.world_props.setdefault(pd.name, pd.default)

        ctx = Ctx(mechanic=m, actor=self.players.get(m.creator))
        for et in m.entities:
            if et.spawn is not None:
                self._initial_spawn(m, et, ctx)
        for zd in m.zones:
            if zd.placed_at_start:
                p = resolve_position(self, zd.at, ctx) or (self.map_size / 2, self.map_size / 2)
                self.create_zone(zd.zone, p[0], p[1], owner=None, duration=None)
        if self.phase == "play":
            self._schedule_every(m)
        if self.day == 1 and m.day == 1:
            for r in m.rules:
                if r.trigger.on == "game_start":
                    self.push(Event("game_start", only=rule_key(m, r)))
        self.log(f"loaded mechanic {m.id}")
        return m

    def _reindex(self) -> None:
        self.items, self.entity_types, self.statuses, self.abilities = {}, {}, {}, {}
        self.zone_defs, self.tile_defs, self.prop_defs, self.player_prop_defs = {}, {}, {}, {}
        self.owner_of = {}
        self.rule_index = {}
        interact_types: set[str] | None = set()
        for m in self.mechanics.values():
            for kind, defs, key in (("item", m.items, "item"), ("entity", m.entities, "type"),
                                    ("status", m.statuses, "status"), ("ability", m.abilities, "ability"),
                                    ("zone", m.zones, "zone"), ("tile", m.tiles, "tile")):
                reg = {"item": self.items, "entity": self.entity_types, "status": self.statuses,
                       "ability": self.abilities, "zone": self.zone_defs, "tile": self.tile_defs}[kind]
                for d in defs:
                    ident = getattr(d, key)
                    reg[ident] = d
                    self.owner_of[(kind, ident)] = m.id
            for pd in m.player_props:
                self.prop_defs[pd.name] = pd
                self.player_prop_defs[pd.name] = pd
            for pd in m.world_props:
                self.prop_defs[pd.name] = pd
            for et in m.entities:
                for pd in et.properties:
                    self.prop_defs.setdefault(pd.name, pd)
            for zd in m.zones:
                for pd in zd.properties:
                    self.prop_defs.setdefault(pd.name, pd)
            for r in m.rules:
                self.rule_index.setdefault(r.trigger.on, []).append((m, r))
                if r.trigger.on == "interact" and interact_types is not None:
                    if r.trigger.entity:
                        interact_types.add(r.trigger.entity)
                    elif r.trigger.tag:
                        interact_types.add("#" + r.trigger.tag)
                    else:
                        interact_types = None
        for lst in self.rule_index.values():
            lst.sort(key=lambda mr: -mr[1].priority)
        if interact_types is not None:
            tags = {t[1:] for t in interact_types if t.startswith("#")}
            interact_types = {t for t in interact_types if not t.startswith("#")}
            interact_types |= {ty for ty, et in self.entity_types.items() if tags & set(et.tags)}
        self.interactable = interact_types
        self.blocking_tiles = {t for t, td in self.tile_defs.items() if not td.walkable}

    def mechanic_of(self, kind: str, ident: str) -> Mechanic | None:
        mid = self.owner_of.get((kind, ident))
        return self.mechanics.get(mid) if mid else None

    def _initial_spawn(self, m: Mechanic, et: EntityType, ctx: Ctx) -> None:
        sp = et.spawn
        n = min(sp.count, et.max_alive)
        if n > 0:
            if sp.pattern == "scatter":
                for _ in range(n):
                    p = resolve_position(self, sp.at, ctx) or self.random_empty()
                    if sp.at not in ("random", "random_empty", "edge"):
                        p = self._jitter(p, sp.radius or 3.0)
                    self.spawn_entity(et.type, p[0], p[1], ctx=ctx)
            else:
                base = resolve_position(self, sp.at, ctx) or (self.map_size / 2, self.map_size / 2)
                for i, p in enumerate(self._pattern(sp.pattern, base, n, sp.radius)):
                    self.spawn_entity(et.type, p[0], p[1], ctx=ctx)
        if sp.respawn_every_s:
            cap = sp.respawn_up_to if sp.respawn_up_to is not None else sp.count
            cap = min(cap, et.max_alive)

            def respawn(mid=m.id, ty=et.type, every=sp.respawn_every_s, cap=cap):
                cur = self.mechanics.get(mid)
                if cur is None or cur.disabled or ty not in self.entity_types:
                    return
                if self.type_counts[ty] < cap:
                    cur_et = self.entity_types[ty]
                    p = resolve_position(self, cur_et.spawn.at if cur_et.spawn else "random_empty",
                                         Ctx(mechanic=cur)) or self.random_empty()
                    if cur_et.spawn and cur_et.spawn.at not in ("random", "random_empty", "edge"):
                        p = self._jitter(p, cur_et.spawn.radius or 3.0)
                    self.spawn_entity(ty, p[0], p[1], ctx=Ctx(mechanic=cur))
                self.schedule(every, respawn)
            self.schedule(sp.respawn_every_s, respawn)

    def _jitter(self, p, r):
        a, d = self.rng.uniform(0, math.tau), r * math.sqrt(self.rng.random())
        return clamp_pos(self, p[0] + math.cos(a) * d, p[1] + math.sin(a) * d)

    def _pattern(self, pattern: str, base, n: int, radius: float | None):
        bx, by = base
        if pattern == "cluster":
            return [self._jitter(base, radius or 3.0) for _ in range(n)]
        if pattern == "ring":
            r = radius or 4.0
            return [clamp_pos(self, bx + math.cos(i / n * math.tau) * r, by + math.sin(i / n * math.tau) * r)
                    for i in range(n)]
        if pattern == "line":
            gap = 2.0
            return [clamp_pos(self, bx + (i - (n - 1) / 2) * gap, by) for i in range(n)]
        # grid
        cols = max(1, math.ceil(math.sqrt(n)))
        gap = 2.0 if radius is None else max(1.0, 2 * radius / cols)
        return [clamp_pos(self, bx + (i % cols - (cols - 1) / 2) * gap,
                          by + (i // cols - (math.ceil(n / cols) - 1) / 2) * gap) for i in range(n)]

    # ------------------------------------------------------------------ #
    # Players
    # ------------------------------------------------------------------ #
    def add_player(self, pid: str, name: str, color: str | None = None) -> Player:
        if color is None:
            used = {p.color for p in self.players.values()}
            color = next((c for c in PALETTE if c not in used), PALETTE[len(self.players) % len(PALETTE)])
        p = Player(id=pid, name=name, color=color)
        p.x, p.y = self.random_empty(near_center=True)
        p.last_active = self.clock
        for pd in self.player_prop_defs.values():
            p.props[pd.name] = pd.default
        self.players[pid] = p
        return p

    def remove_player(self, pid: str) -> None:
        self.players.pop(pid, None)
        self.zone_membership.pop(pid, None)

    def active_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive and p.connected]

    def random_empty(self, near_center: bool = False) -> tuple[float, float]:
        m = self.map_size
        for i in range(40):
            if near_center:
                r = 3 + i * 0.25
                x = m / 2 + self.rng.uniform(-r, r)
                y = m / 2 + self.rng.uniform(-r, r)
            else:
                x, y = self.rng.uniform(1, m - 1), self.rng.uniform(1, m - 1)
            x, y = clamp_pos(self, x, y)
            if self._is_empty(x, y):
                return (x, y)
        return clamp_pos(self, self.rng.uniform(1, m - 1), self.rng.uniform(1, m - 1))

    def _is_empty(self, x: float, y: float) -> bool:
        for e in self.entities.values():
            if e.solid and math.hypot(e.x - x, e.y - y) < e.radius + 1.0:
                return False
        for p in self.players.values():
            if math.hypot(p.x - x, p.y - y) < 1.0:
                return False
        return self.tiles.at(x, y) not in self.blocking_tiles

    def player_has_ability(self, p: Player, aid: str) -> bool:
        ad = self.abilities.get(aid)
        m = self.mechanic_of("ability", aid)
        if ad is None or m is None or ad.disabled or m.disabled:
            return False
        if ad.available_to == "everyone":
            return True
        if ad.available_to == "creator":
            return p.id == m.creator or aid in p.granted_abilities
        return aid in p.granted_abilities

    # ------------------------------------------------------------------ #
    # Props, points, items
    # ------------------------------------------------------------------ #
    def prop_default(self, name: str) -> Any:
        pd = self.prop_defs.get(name)
        if pd is None:
            return 0
        if pd.type in ("player", "entity"):
            return None
        return pd.default if pd.default is not None else 0

    def read_prop(self, name: str, value: Any) -> Any:
        pd = self.prop_defs.get(name)
        if pd is not None and pd.type in ("player", "entity") and isinstance(value, str):
            return self.players.get(value) or self.entities.get(value)
        return value

    def get_prop(self, o: Any, name: str) -> Any:
        return get_attr(self, o, name)

    def _coerce_prop(self, name: str, value: Any) -> Any:
        if isinstance(value, (Player, Entity, ZoneInstance)):
            value = value.id
        pd = self.prop_defs.get(name)
        kind = pd.type if pd else ("number" if isinstance(value, (int, float)) else "string")
        if kind == "number":
            v = num(value)
            if pd is not None and pd.min is not None:
                v = max(pd.min, v)
            if pd is not None and pd.max is not None:
                v = min(pd.max, v)
            return tidy(round(v, 4))
        if kind == "bool":
            return truthy(value)
        if kind == "string":
            return to_text(value)[:280]
        return value if isinstance(value, str) or value is None else None

    def set_prop(self, o: Any, name: str, value: Any, ctx: Ctx | None = None) -> None:
        depth = ctx.depth + 1 if ctx else 0
        if isinstance(o, Player):
            if name in ("hp", "max_hp"):
                old = getattr(o, name)
                v = num(value)
                if name == "max_hp":
                    o.max_hp = max(1.0, v)
                    o.hp = min(o.hp, o.max_hp)
                else:
                    o.hp = max(0.0, min(o.max_hp, v))
                new = getattr(o, name)
                if name == "hp" and o.hp <= 0 and o.alive:
                    self._defeat(o, ctx.actor if ctx else None, depth)
            elif name == "team":
                old, new = o.team, to_text(value)[:24]
                o.team = new
            elif name in self.items:
                self.give_item(o, name, int(num(value)) - o.items.get(name, 0), ctx)
                return
            elif name in ("points", "name", "x", "y", "id", "speed", "size", "vision"):
                self.log_once(f"ro:{name}", f"property '{name}' is read-only")
                return
            else:
                old = o.props.get(name, self.prop_default(name))
                new = self._coerce_prop(name, value)
                o.props[name] = new
        elif isinstance(o, Entity):
            if name in ("hp", "max_hp"):
                if o.hp is None:
                    return
                old = getattr(o, name)
                v = num(value)
                if name == "max_hp":
                    o.max_hp = max(1.0, v)
                    o.hp = min(o.hp, o.max_hp)
                else:
                    o.hp = max(0.0, min(o.max_hp or v, v))
                new = getattr(o, name)
                if name == "hp" and o.hp <= 0:
                    self._defeat(o, ctx.actor if ctx else None, depth)
            elif name in ("x", "y", "id", "type", "owner", "age"):
                self.log_once(f"ro:{name}", f"property '{name}' is read-only")
                return
            else:
                old = o.props.get(name, self.prop_default(name))
                new = self._coerce_prop(name, value)
                o.props[name] = new
        elif isinstance(o, ZoneInstance):
            old = o.props.get(name, self.prop_default(name))
            new = self._coerce_prop(name, value)
            o.props[name] = new
        elif isinstance(o, WorldProxy):
            if name in ("day", "time", "time_left", "player_count", "phase"):
                return
            old = self.world_props.get(name, self.prop_default(name))
            new = self._coerce_prop(name, value)
            self.world_props[name] = new
        else:
            return
        if old != new:
            data = {"property": name, "old": old, "new": new}
            if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                data["delta"] = tidy(new - old)
            self.push(Event("property_changed", o, o, data, depth))

    def change_points(self, p: Player, delta: float, reason: str | None, mechanic_id: str | None) -> None:
        p.points = max(0.0, round(p.points + delta, 2))
        self.send(None, {"t": "points", "player_id": p.id, "delta": tidy(delta), "total": tidy(p.points),
                         "reason": reason, "mechanic": mechanic_id})
        self.push(Event("points_changed", p, None, {"amount": tidy(delta), "reason": reason or "",
                                                    "mechanic": mechanic_id or ""}))
        if p.points >= self.win_points and not self.winner_ids:
            self.winner_ids = [p.id]

    def award_points(self, p: Player, amount: float, reason: str) -> None:
        """Bypasses the ledger (author bonus, debug)."""
        if amount:
            self.change_points(p, amount, reason, None)

    def give_item(self, p: Player, item: str, amount: int, ctx: Ctx | None = None) -> int:
        idf = self.items.get(item)
        if idf is None or amount == 0:
            return 0
        cur = p.items.get(item, 0)
        if amount > 0:
            amount = int(round(amount * max(0.0, p.stats.get("item_gain_mult", 1.0))))
            new = min(idf.stack_max, cur + amount)
        else:
            new = max(0, cur + amount)
        if new == cur:
            return 0
        if new:
            p.items[item] = new
        else:
            p.items.pop(item, None)
        if new > cur:
            self.push(Event("item_gained", p, None, {"item": item, "amount": new - cur},
                            ctx.depth + 1 if ctx else 0))
        return new - cur

    # ------------------------------------------------------------------ #
    # Entities, zones, tiles
    # ------------------------------------------------------------------ #
    def new_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    def spawn_entity(self, etype: str, x: float, y: float, owner: str | None = None,
                     props: dict | None = None, ctx: Ctx | None = None) -> Entity | None:
        et = self.entity_types.get(etype)
        m = self.mechanic_of("entity", etype)
        if et is None or m is None:
            return None
        if self.type_counts[etype] >= et.max_alive:
            return None
        if len(self.entities) >= self.max_entities:
            self.log_once("cap:entities", f"entity cap {self.max_entities} reached")
            return None
        x, y = clamp_pos(self, x, y)
        e = Entity(id=self.new_id("e"), type=etype, mechanic_id=m.id, x=x, y=y,
                   size=et.visual.size, square=et.visual.shape == "square", solid=et.solid,
                   owner=owner, born_at=self.clock, spawn_x=x, spawn_y=y)
        e.stats = dict(BASE_STATS, speed=1.0)
        for pd in et.properties:
            e.props[pd.name] = pd.default
        ectx = Ctx(mechanic=m, actor=ctx.actor if ctx else None, it=e,
                   event=ctx.event if ctx else None, target=ctx.target if ctx else None)
        for k, v in (props or {}).items():
            e.props[k] = self._coerce_prop(k, v)
        if et.hp is not None:
            e.hp = e.max_hp = max(1.0, num(evaluate(self, et.hp, ectx)))
        if et.lifetime_s is not None:
            life = num(evaluate(self, et.lifetime_s, ectx))
            if life > 0:
                e.expires_at = self.clock + life
        if et.behavior.kind in ("projectile", "bounce"):
            if et.behavior.kind == "projectile":
                e.brain["projectile"] = True
            d = ctx.event.get("direction") if ctx else None
            if isinstance(d, (int, float)) and not isinstance(d, bool):
                e.facing = float(d)
            elif ctx is not None and isinstance(ctx.actor, (Player, Entity)):
                e.facing = ctx.actor.facing
            else:
                e.facing = self.rng.uniform(0, 360)
        self.entities[e.id] = e
        self.type_counts[etype] += 1
        self.push(Event("spawn", ctx.actor if ctx else None, e, {"entity": etype},
                        ctx.depth + 1 if ctx else 0))
        return e

    def destroy_entity(self, e: Entity, actor: Any = None, depth: int = 0) -> None:
        if e.dead:
            return
        e.dead = True
        self.push(Event("destroy", actor, e, {"entity": e.type}, depth + 1))
        self.entities.pop(e.id, None)
        self.type_counts[e.type] -= 1

    def teleport(self, o: Player | Entity, x: float, y: float) -> None:
        o.x, o.y = clamp_pos(self, x, y)
        o.motion = None

    def create_zone(self, zone: str, x: float, y: float, owner: str | None = None,
                    duration: float | None = None) -> ZoneInstance | None:
        zd = self.zone_defs.get(zone)
        m = self.mechanic_of("zone", zone)
        if zd is None or m is None:
            return None
        if sum(1 for z in self.zones.values() if z.zone == zone) >= MAX_ZONES_PER_DEF:
            return None
        z = ZoneInstance(id=self.new_id("z"), zone=zone, mechanic_id=m.id, x=x, y=y, shape=zd.shape,
                         radius=zd.radius or 3.0, width=zd.width or 2 * (zd.radius or 3.0),
                         height=zd.height or 2 * (zd.radius or 3.0), owner=owner,
                         expires_at=self.clock + duration if duration and duration > 0 else None)
        for pd in zd.properties:
            z.props[pd.name] = pd.default
        self.zones[z.id] = z
        return z

    def remove_zones(self, zone: str) -> None:
        for zid in [k for k, z in self.zones.items() if z.zone == zone]:
            del self.zones[zid]

    def in_zone(self, x: float, y: float, zone: str) -> bool:
        return any(z.zone == zone and z.contains(x, y) for z in self.zones.values())

    def paint(self, tile: str, x: float, y: float, r: float, duration: float | None) -> None:
        if tile not in self.tile_defs:
            return
        exp = self.clock + duration if duration and duration > 0 else None
        rr = max(r, 0.5)
        for tx in range(int(math.floor(x - rr)), int(math.ceil(x + rr)) + 1):
            for ty in range(int(math.floor(y - rr)), int(math.ceil(y + rr)) + 1):
                if 0 <= tx < self.map_size and 0 <= ty < self.map_size and \
                        math.hypot(tx + 0.5 - x, ty + 0.5 - y) <= rr:
                    self.tiles.paint(tx, ty, tile, exp)

    # ------------------------------------------------------------------ #
    # Combat & statuses
    # ------------------------------------------------------------------ #
    def damage(self, o: Player | Entity, amount: float, attacker: Any = None, depth: int = 0) -> None:
        if isinstance(o, Entity) and (o.hp is None or o.dead):
            return
        if isinstance(o, Player) and not o.alive:
            return
        if amount > 0:
            if isinstance(attacker, (Player, Entity)):
                amount *= max(0.0, attacker.stats.get("damage_mult", 1.0))
            amount /= max(0.1, o.stats.get("defense_mult", 1.0))
        cap = o.max_hp if o.max_hp is not None else o.hp
        o.hp = max(0.0, min(cap, o.hp - amount))
        if amount > 0:
            self.push(Event("damaged", attacker, o, {"amount": tidy(round(amount, 2))}, depth + 1))
        if o.hp <= 0:
            self._defeat(o, attacker, depth)

    def _defeat(self, o: Player | Entity, attacker: Any, depth: int) -> None:
        self.push(Event("defeated", attacker, o, {}, depth + 1))
        if isinstance(o, Player):
            o.defeated_until = self.clock + RESPAWN_S
            o.motion = None
        else:
            self.destroy_entity(o, attacker, depth)

    def apply_status(self, o: Player | Entity, sid: str, duration: float | None = None, stacks: int = 1,
                     source: Any = None, depth: int = 0) -> None:
        sd = self.statuses.get(sid)
        m = self.mechanic_of("status", sid)
        if sd is None or m is None:
            return
        if "immune" in o.flags:
            return
        ctx = Ctx(mechanic=m, actor=o, target=o)
        dur = duration if duration is not None else num(evaluate(self, sd.duration_s, ctx))
        dur = max(0.1, min(600.0, dur))
        cur = next((s for s in o.statuses if s.status == sid), None)
        if cur is not None:
            if sd.stacking == "refresh":
                cur.expires_at = self.clock + dur
            elif sd.stacking == "stack":
                cur.stacks = min(sd.max_stacks, cur.stacks + stacks)
                cur.expires_at = self.clock + dur
            elif sd.stacking == "extend":
                cur.expires_at = min(cur.expires_at + dur, self.clock + 600)
            self.recompute_stats(o)
            return
        inst = StatusInstance(status=sid, mechanic_id=m.id, stacks=min(stacks, sd.max_stacks),
                              expires_at=self.clock + dur,
                              next_tick_at=self.clock + sd.tick_s if sd.tick_s else None,
                              source=getattr(source, "id", None))
        o.statuses.append(inst)
        self.recompute_stats(o)
        if sd.on_apply:
            self.run(sd.on_apply, ctx.with_(depth=depth + 1), key=f"{m.id}/status:{sid}")
        self.push(Event("status_applied", o, o, {"status": sid, "from": source}, depth + 1))

    def remove_status(self, o: Player | Entity, sid: str) -> None:
        before = len(o.statuses)
        o.statuses = [s for s in o.statuses if s.status != sid]
        if len(o.statuses) != before:
            self.recompute_stats(o)

    def recompute_stats(self, o: Player | Entity) -> None:
        base = dict(BASE_STATS)
        if isinstance(o, Entity):
            base["speed"] = 1.0
        flags: set[str] = set()
        adds: list = []
        muls: list = []
        sets: list = []
        for inst in o.statuses:
            sd = self.statuses.get(inst.status)
            if sd is None:
                continue
            flags.update(sd.flags)
            if not sd.modifiers:
                continue
            m = self.mechanics.get(inst.mechanic_id)
            ctx = Ctx(mechanic=m, actor=o, target=o)
            for mod in sd.modifiers:
                try:
                    v = num(evaluate(self, mod.value, ctx))
                except Exception:  # noqa: BLE001
                    continue
                if mod.op == "add":
                    adds.append((mod.stat, v * inst.stacks))
                elif mod.op == "mul":
                    muls.append((mod.stat, v ** inst.stacks))
                else:
                    sets.append((mod.stat, v))
        for s, v in adds:
            base[s] = base.get(s, 0) + v
        for s, v in muls:
            base[s] = base.get(s, 0) * v
        for s, v in sets:
            base[s] = v
        base["speed"] = max(0.0, min(base["speed"], 30.0))
        base["size"] = max(0.25, min(base["size"], 4.0))
        if isinstance(o, Player):
            for z in self.zones.values():
                zd = self.zone_defs.get(z.zone)
                if zd is not None and zd.hides_players and z.contains(o.x, o.y):
                    flags.add("hidden")
                    break
        o.stats = base
        o.flags = flags

    def _process_statuses(self, o: Player | Entity) -> None:
        for inst in list(o.statuses):
            sd = self.statuses.get(inst.status)
            m = self.mechanics.get(inst.mechanic_id)
            if sd is None or m is None:
                o.statuses.remove(inst)
                continue
            key = f"{m.id}/status:{inst.status}"
            ctx = Ctx(mechanic=m, actor=o, target=o)
            if inst.next_tick_at is not None and sd.tick_s:
                n = 0
                while inst.next_tick_at <= self.clock and n < 5:
                    inst.next_tick_at += sd.tick_s
                    n += 1
                    if sd.on_tick:
                        self.run(sd.on_tick, ctx, key=key)
            if self.clock >= inst.expires_at and inst in o.statuses:
                o.statuses.remove(inst)
                self.recompute_stats(o)
                if sd.on_expire:
                    self.run(sd.on_expire, ctx, key=key)
                self.push(Event("status_expired", o, o, {"status": inst.status}))

    # ------------------------------------------------------------------ #
    # Prompts & in-game votes
    # ------------------------------------------------------------------ #
    def open_prompt(self, p: Player, question: str, options: list[ChoiceOption], timeout: float,
                    default: int | None, ctx: Ctx) -> None:
        mine = [k for k, v in self.prompts.items() if v["player"] == p.id]
        if len(mine) >= MAX_PROMPTS_PER_PLAYER:
            self._close_prompt(mine[0], run_default=False)
        qid = self.new_id("q")
        self.prompts[qid] = {"player": p.id, "options": options, "default": default, "ctx": ctx,
                             "expires_at": self.clock + timeout}
        self.send([p.id], {"t": "prompt", "prompt_id": qid, "question": question,
                           "options": [o.label for o in options], "timeout": timeout, "vote": False})

    def _close_prompt(self, qid: str, run_default: bool) -> None:
        pr = self.prompts.pop(qid, None)
        if pr is None:
            return
        self.send([pr["player"]], {"t": "prompt_close", "prompt_id": qid})
        d = pr["default"]
        p = self.players.get(pr["player"])
        if run_default and p is not None and d is not None and 0 <= d < len(pr["options"]):
            ctx = pr["ctx"]
            self.run(pr["options"][d].do, ctx.with_(actor=p, flow={"stop": False}),
                     key=f"{ctx.mechanic.id if ctx.mechanic else '_'}/prompt")

    def open_vote(self, voters: list[Player], question: str, options: list[ChoiceOption],
                  duration: float, tie: str, ctx: Ctx) -> None:
        vid = self.new_id("v")
        self.votes[vid] = {"voters": [p.id for p in voters], "options": options, "tie": tie, "ctx": ctx,
                           "ends_at": self.clock + duration, "choices": {}, "question": question}
        self.send([p.id for p in voters], {"t": "prompt", "prompt_id": vid, "question": question,
                                           "options": [o.label for o in options], "timeout": duration,
                                           "vote": True})

    def _resolve_vote(self, vid: str) -> None:
        v = self.votes.pop(vid, None)
        if v is None:
            return
        self.send(v["voters"], {"t": "prompt_close", "prompt_id": vid})
        counts = Counter(v["choices"].values())
        if not counts:
            return
        top = max(counts.values())
        best = sorted(i for i, c in counts.items() if c == top)
        if len(best) > 1:
            if v["tie"] == "none":
                self.send(v["voters"], {"t": "message", "text": f"Vote tied: {v['question']}",
                                        "style": "log", "from": None})
                return
            win = best[0] if v["tie"] == "first" else self.rng.choice(best)
        else:
            win = best[0]
        opt = v["options"][win]
        self.send(v["voters"], {"t": "message", "text": f"🗳️ Vote result: {opt.label}", "style": "toast",
                                "from": None})
        ctx = v["ctx"]
        for pid, choice in v["choices"].items():
            p = self.players.get(pid)
            if choice == win and p is not None:
                self.run(opt.do, ctx.with_(actor=p, flow={"stop": False}),
                         key=f"{ctx.mechanic.id if ctx.mechanic else '_'}/vote")

    def close_all_prompts(self) -> None:
        for qid in list(self.prompts):
            self._close_prompt(qid, run_default=False)
        for vid in list(self.votes):
            v = self.votes.pop(vid)
            self.send(v["voters"], {"t": "prompt_close", "prompt_id": vid})

    # ------------------------------------------------------------------ #
    # Execution helpers
    # ------------------------------------------------------------------ #
    def push(self, ev: Event) -> None:
        self.queue.push(ev)

    def run(self, effects: list, ctx: Ctx, key: str | None = None) -> None:
        if key is None:
            run_effects(self, effects, ctx)
            return
        if key in self.disabled_rules:
            return
        try:
            run_effects(self, effects, ctx)
        except Exception as e:  # noqa: BLE001
            self.rule_error(key, e)

    def schedule(self, delay: float, fn: Callable[[], None]) -> None:
        heapq.heappush(self.timers, (self.clock + max(0.0, delay), next(self._seq), fn))

    def _schedule_every(self, m: Mechanic) -> None:
        gen = self._every_gen
        for r in m.rules:
            if r.trigger.on != "every":
                continue
            key = rule_key(m, r)

            def fire(key=key, every=r.trigger.every_s, gen=gen):
                if gen != self._every_gen:
                    return
                self.push(Event("every", only=key))
                self.schedule(every, fire)
            self.schedule(r.trigger.every_s, fire)

    # ------------------------------------------------------------------ #
    # Day cycle
    # ------------------------------------------------------------------ #
    @property
    def time_left(self) -> float:
        return max(0.0, self.play_seconds - self.time)

    def start_day(self, day: int, play_seconds: float) -> None:
        self.day = day
        self.time = 0.0
        self.play_seconds = play_seconds
        self.phase = "play"
        self.limiter.reset_day()
        self._every_gen += 1
        for m in self.mechanics.values():
            self._schedule_every(m)
        self.queue.new_tick()
        self.push(Event("day_start"))
        self.queue.drain()

    def end_day(self) -> None:
        self.queue.new_tick()
        self.push(Event("day_end"))
        self.queue.drain()
        self._every_gen += 1
        self.close_all_prompts()
        for p in self.players.values():
            p.input_dx = p.input_dy = 0.0
            p.input_queue.clear()
            p.motion = None
        self.phase = "night"

    # ------------------------------------------------------------------ #
    # Client actions (queued, applied at the start of the next tick)
    # ------------------------------------------------------------------ #
    def set_input(self, pid: str, dx: float, dy: float, facing: float | None = None, seq: int = 0) -> None:
        p = self.players.get(pid)
        if p is None:
            return
        if not (math.isfinite(dx) and math.isfinite(dy)):
            return
        p.input_queue.append((max(-1.0, min(1.0, dx)), max(-1.0, min(1.0, dy)), seq))
        if facing is not None and math.isfinite(facing):
            p.facing = facing

    def queue_action(self, kind: str, *args) -> None:
        if len(self.actions) < 200:
            self.actions.append((kind, args))

    def _apply_actions(self) -> None:
        acts, self.actions = self.actions, []
        for kind, args in acts:
            try:
                getattr(self, f"do_{kind}")(*args)
            except Exception as e:  # noqa: BLE001
                self.log(f"action {kind} failed: {type(e).__name__}: {e}")

    def toast(self, pid: str, text: str) -> None:
        self.send([pid], {"t": "message", "text": text, "style": "toast", "from": None})

    def blocked_by_zone(self, p: Player, attr: str) -> bool:
        for z in self.zones.values():
            zd = self.zone_defs.get(z.zone)
            if zd is not None and getattr(zd, attr) and z.contains(p.x, p.y):
                return True
        return False

    def nearest_interactable(self, p: Player) -> Entity | None:
        best, bd = None, math.inf
        rng_mult = max(0.1, p.stats.get("interact_range", 1.0))
        for e in self.entity_hash.query(p.x, p.y, 10 * rng_mult + physics.MAX_ENTITY_RADIUS):
            if e.dead or (self.interactable is not None and e.type not in self.interactable):
                continue
            et = self.entity_types.get(e.type)
            if et is None or et.interactable_by == "nobody":
                continue
            if et.interactable_by == "owner" and e.owner != p.id:
                continue
            if et.interactable_by == "not_owner" and e.owner == p.id:
                continue
            if et.interactable_by == "creator":
                m = self.mechanics.get(e.mechanic_id)
                if m is None or m.creator != p.id:
                    continue
            d = math.hypot(e.x - p.x, e.y - p.y) - e.radius - p.radius
            if d <= et.interact_range * rng_mult and d < bd:
                best, bd = e, d
        return best

    def do_interact(self, pid: str) -> None:
        p = self.players.get(pid)
        if p is None or not p.alive or self.phase != "play":
            return
        if "disarmed" in p.flags or self.blocked_by_zone(p, "no_interact"):
            return
        e = self.nearest_interactable(p)
        if e is None:
            return
        p.last_active = self.clock
        self.push(Event("interact", p, e, {"entity": e.type}))

    def do_use_item(self, pid: str, item: str) -> None:
        p = self.players.get(pid)
        idf = self.items.get(item)
        if p is None or idf is None or not p.alive or self.phase != "play":
            return
        if p.items.get(item, 0) <= 0:
            return
        if not idf.usable:
            self.toast(pid, f"{idf.name} can't be used")
            return
        p.last_active = self.clock
        fired = dispatch(self, Event("use_item", p, None, {"item": item}))
        if fired and idf.consumed_on_use:
            self.give_item(p, item, -1)

    def do_use_ability(self, pid: str, aid: str, target_id: str | None = None,
                       point: dict | None = None, direction: float | None = None) -> None:
        p = self.players.get(pid)
        ad = self.abilities.get(aid)
        m = self.mechanic_of("ability", aid)
        if p is None or ad is None or m is None or not p.alive or self.phase != "play":
            return
        if not self.player_has_ability(p, aid):
            return
        key = f"{m.id}/ability:{aid}"
        if key in self.disabled_rules:
            return
        if "silenced" in p.flags:
            return self.toast(pid, "You are silenced")
        if self.blocked_by_zone(p, "no_abilities"):
            return self.toast(pid, "Abilities don't work here")
        if exclusive_blocked(self, m, p):
            creator = self.players.get(m.creator)
            return self.toast(pid, f"Only {creator.name if creator else 'its creator'} can use {ad.name} for now")
        cd_mult = p.stats.get("cooldown_mult", 1.0)
        if not self.limiter.check(key, ad.limits, pid, self.clock, cd_mult):
            return self.toast(pid, f"{ad.name} isn't ready")
        ctx = Ctx(mechanic=m, actor=p, target=None, event={"ability": aid})
        rng_ = max(0.0, num(evaluate(self, ad.range, ctx)))
        target: Any = None
        if ad.targeting in ("none", "self"):
            target = p
        elif ad.targeting == "direction":
            ang = direction if isinstance(direction, (int, float)) and math.isfinite(direction) else p.facing
            ctx.event["direction"] = ang
            ctx.event["point"] = {"x": p.x + math.cos(math.radians(ang)) * rng_,
                                  "y": p.y + math.sin(math.radians(ang)) * rng_}
            p.facing = ang
        elif ad.targeting == "point":
            px, py = (num(point.get("x")), num(point.get("y"))) if isinstance(point, dict) else (p.x, p.y)
            d = math.hypot(px - p.x, py - p.y)
            if d > rng_ > 0:
                px, py = p.x + (px - p.x) / d * rng_, p.y + (py - p.y) / d * rng_
            px, py = clamp_pos(self, px, py)
            ctx.event["point"] = {"x": px, "y": py}
            ctx.event["direction"] = math.degrees(math.atan2(py - p.y, px - p.x))
        elif ad.targeting == "entity":
            e = self.entities.get(target_id or "")
            if e is None or math.hypot(e.x - p.x, e.y - p.y) - e.radius > rng_:
                return self.toast(pid, "No target in range")
            target = e
        elif ad.targeting == "player":
            q = self.players.get(target_id or "")
            if q is None or q is p or not q.alive or math.hypot(q.x - p.x, q.y - p.y) > rng_ + q.radius:
                return self.toast(pid, "No player in range")
            target = q
        ctx.target = target
        if target is not None:
            ctx.event["target"] = target
        # costs
        disc = m.creator_perk.discount if p.id == m.creator else 0.0
        costs = []
        for c in ad.cost:
            amt = max(0.0, num(evaluate(self, c.amount, ctx)) * (1 - disc))
            if c.what in self.items:
                amt = math.ceil(amt - 1e-9)
                have = p.items.get(c.what, 0)
            elif c.what == "points":
                have = p.points
            else:
                have = num(self.get_prop(p, c.what))
            if have < amt:
                label = self.items[c.what].name if c.what in self.items else c.what
                return self.toast(pid, f"{ad.name} needs {tidy(amt)} {label}")
            costs.append((c.what, amt))
        try:
            if not all(truthy(evaluate(self, cond, ctx)) for cond in ad.if_):
                return
        except Exception as e:  # noqa: BLE001
            return self.rule_error(key, e)
        for what, amt in costs:
            if amt <= 0:
                continue
            if what in self.items:
                self.give_item(p, what, -int(amt))
            elif what == "points":
                self.change_points(p, -min(amt, p.points), f"Used {ad.name}", m.id)
            else:
                self.set_prop(p, what, num(self.get_prop(p, what)) - amt, ctx)
        self.limiter.consume(key, pid, self.clock)
        uses = p.granted_abilities.get(aid, None)
        if aid in p.granted_abilities and uses is not None:
            if uses <= 1:
                p.granted_abilities.pop(aid)
            else:
                p.granted_abilities[aid] = uses - 1
        p.last_active = self.clock
        self.fired[m.id] += 1
        self.run(ad.do, ctx, key=key)
        self.push(Event("use_ability", p, target, {"ability": aid}))

    def do_answer_prompt(self, pid: str, prompt_id: str, option: int) -> None:
        if prompt_id in self.votes:
            v = self.votes[prompt_id]
            if pid in v["voters"] and isinstance(option, int) and 0 <= option < len(v["options"]):
                v["choices"][pid] = option
                if len(v["choices"]) == len(v["voters"]):
                    self._resolve_vote(prompt_id)
            return
        pr = self.prompts.get(prompt_id)
        if pr is None or pr["player"] != pid:
            return
        if not isinstance(option, int) or not 0 <= option < len(pr["options"]):
            return
        del self.prompts[prompt_id]
        p = self.players.get(pid)
        if p is None:
            return
        ctx = pr["ctx"]
        self.run(pr["options"][option].do, ctx.with_(actor=p, flow={"stop": False}),
                 key=f"{ctx.mechanic.id if ctx.mechanic else '_'}/prompt")

    def do_chat(self, pid: str, text: str) -> None:
        p = self.players.get(pid)
        if p is None:
            return
        p.last_active = self.clock
        self.push(Event("chat", p, None, {"text": text}))

    # ------------------------------------------------------------------ #
    # Tick
    # ------------------------------------------------------------------ #
    def tick(self, dt: float) -> None:
        self.tick_no += 1
        self.effects_this_tick = 0
        self.queue.new_tick()
        self.clock += dt
        self.time += dt

        live = [e for e in self.entities.values() if not e.dead]
        self.solid_hash.build(e for e in live if e.solid)
        self.entity_hash.build(live)

        for o in itertools.chain(self.players.values(), live):
            self.recompute_stats(o)

        # 1. inputs
        self._apply_actions()
        # 2. movement
        physics.step_players(self, dt)
        # 3. entity behaviours
        step_behaviors(self, dt)
        self.entity_hash.build(e for e in self.entities.values() if not e.dead)
        # 4. contacts
        physics.detect_contacts(self)
        # 5. timers
        self._process_timers()
        # 6. events
        self.queue.drain()

    def _process_timers(self) -> None:
        now = self.clock
        n = 0
        while self.timers and self.timers[0][0] <= now and n < 2000:
            _, _, fn = heapq.heappop(self.timers)
            n += 1
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.log(f"timer failed: {type(e).__name__}: {e}")
        for o in itertools.chain(list(self.players.values()), list(self.entities.values())):
            if o.statuses:
                self._process_statuses(o)
        for e in list(self.entities.values()):
            if e.expires_at is not None and now >= e.expires_at:
                self.destroy_entity(e, None)
        for zid in [k for k, z in self.zones.items() if z.expires_at is not None and now >= z.expires_at]:
            del self.zones[zid]
        self.tiles.expire(now)
        for p in self.players.values():
            if p.defeated_until is not None and now >= p.defeated_until:
                p.defeated_until = None
                p.hp = p.max_hp
                p.x, p.y = self.random_empty()
        for qid in [k for k, v in self.prompts.items() if now >= v["expires_at"]]:
            self._close_prompt(qid, run_default=True)
        for vid in [k for k, v in self.votes.items() if now >= v["ends_at"]]:
            self._resolve_vote(vid)

    # ------------------------------------------------------------------ #
    # Client views
    # ------------------------------------------------------------------ #
    def snapshot_common(self) -> dict:
        players = []
        for p in self.players.values():
            players.append({
                "id": p.id, "name": p.name, "color": p.color, "x": round(p.x, 2), "y": round(p.y, 2),
                "facing": round(p.facing), "points": tidy(p.points), "hp": tidy(round(p.hp, 1)),
                "max_hp": tidy(p.max_hp), "size": round(p.stats.get("size", 1.0), 2),
                "statuses": [s.status for s in p.statuses], "visual": p.visual or None,
                "flags": sorted(p.flags & {"marked", "invisible", "hidden", "frozen", "ghost"}),
                "connected": p.connected, "dead": not p.alive,
            })
        ents = []
        for e in self.entities.values():
            if e.dead:
                continue
            et = self.entity_types.get(e.type)
            visible = {pd.name: e.props.get(pd.name) for pd in (et.properties if et else [])
                       if pd.visible_to == "everyone"}
            ents.append({"id": e.id, "type": e.type, "x": round(e.x, 2), "y": round(e.y, 2),
                         "owner": e.owner, "hp": tidy(round(e.hp, 1)) if e.hp is not None else None,
                         "max_hp": tidy(e.max_hp) if e.max_hp is not None else None,
                         "props": visible or None, "visual": e.visual or None,
                         "statuses": [s.status for s in e.statuses] or None})
        zones = [{"id": z.id, "zone": z.zone, "x": round(z.x, 2), "y": round(z.y, 2), "shape": z.shape,
                  "radius": z.radius, "width": z.width, "height": z.height, "owner": z.owner}
                 for z in self.zones.values()]
        return {"tick": self.tick_no, "players": players, "entities": ents, "zones": zones,
                "tiles_delta": self.tiles.take_delta()}

    def snapshot_for(self, pid: str, common: dict) -> dict:
        snap = dict(common)
        snap["players"] = [p for p in common["players"]
                           if p["id"] == pid or not ({"invisible", "hidden"} & set(p["flags"]))]
        snap["hud_values"] = self.hud_values(pid)
        snap["me"] = self.personal(pid)
        return snap

    def personal(self, pid: str) -> dict | None:
        p = self.players.get(pid)
        if p is None:
            return None
        abilities = []
        for aid, ad in self.abilities.items():
            if not self.player_has_ability(p, aid):
                continue
            m = self.mechanic_of("ability", aid)
            key = f"{m.id}/ability:{aid}"
            cd = self.limiter.cooldown_left(key, ad.limits, pid, self.clock, p.stats.get("cooldown_mult", 1.0))
            abilities.append({"id": aid, "cooldown": round(cd, 1),
                              "cooldown_total": tidy((ad.limits.cooldown_s or 0) * p.stats.get("cooldown_mult", 1.0)),
                              "uses": p.granted_abilities.get(aid),
                              "left_today": self.limiter.uses_left_today(key, ad.limits, pid)})
        props = {k: v for k, v in p.props.items()
                 if (pd := self.player_prop_defs.get(k)) is not None and pd.visible_to != "nobody"}
        return {"items": dict(p.items), "abilities": abilities, "props": props, "ack": p.input_seq,
                "speed": round(p.stats.get("speed", 4.0) * physics.terrain_mult(self, p.x, p.y), 2),
                "flags": sorted(p.flags), "statuses": [
                    {"id": s.status, "stacks": s.stacks, "left": round(max(0, s.expires_at - self.clock), 1)}
                    for s in p.statuses]}

    def hud_values(self, pid: str) -> list[dict]:
        p = self.players.get(pid)
        out = []
        for m in self.mechanics.values():
            if m.disabled or not m.hud:
                continue
            for i, h in enumerate(m.hud):
                actor = p if h.visible_to == "owner" else None
                ctx = Ctx(mechanic=m, actor=actor, it=actor)
                try:
                    if h.kind == "leaderboard":
                        rows = []
                        for q in self.players.values():
                            c = Ctx(mechanic=m, actor=q, it=q)
                            rows.append({"name": q.name, "value": _jsonable(evaluate(self, h.value, c))})
                        rows.sort(key=lambda r: -num(r["value"]))
                        value: Any = rows[:5]
                    else:
                        value = _jsonable(evaluate(self, h.value, ctx))
                    mx = _jsonable(evaluate(self, h.max, ctx)) if h.max is not None else None
                except Exception:  # noqa: BLE001
                    value, mx = None, None
                out.append({"id": f"{m.id}:{i}", "kind": h.kind, "label": h.label, "value": value,
                            "max": mx, "anchor": h.anchor})
        return out

    def defs(self) -> dict:
        def vis(v):
            return v.model_dump() if v is not None else None
        abilities = []
        for aid, ad in self.abilities.items():
            ctx = Ctx(mechanic=self.mechanic_of("ability", aid))
            costs = [{"what": c.what, "amount": _jsonable(evaluate(self, c.amount, ctx))} for c in ad.cost]
            abilities.append({"id": aid, "name": ad.name, "description": ad.description, "icon": ad.icon,
                              "targeting": ad.targeting, "range": _jsonable(evaluate(self, ad.range, ctx)),
                              "cost": costs, "cooldown": ad.limits.cooldown_s})
        return {
            "map_size": self.map_size,
            "entities": {t: {"name": et.name, "visual": vis(et.visual), "solid": et.solid,
                             "interactable": (self.interactable is None or t in self.interactable)
                             and et.interactable_by != "nobody",
                             "props": [{"name": pd.name, "label": pd.label, "emoji": pd.emoji}
                                       for pd in et.properties if pd.visible_to == "everyone"]}
                         for t, et in self.entity_types.items()},
            "items": {i: {"name": d.name, "visual": vis(d.visual), "usable": d.usable}
                      for i, d in self.items.items()},
            "statuses": {s: {"name": d.name, "visual": vis(d.visual)} for s, d in self.statuses.items()},
            "abilities": abilities,
            "tiles": {t: {"color": d.color, "walkable": d.walkable} for t, d in self.tile_defs.items()},
            "zones": {z: {"name": d.name, "visual": vis(d.visual), "shape": d.shape}
                      for z, d in self.zone_defs.items()},
            "props": {k: {"label": pd.label or k.replace("_", " "), "emoji": pd.emoji,
                          "visible_to": pd.visible_to}
                      for k, pd in self.player_prop_defs.items() if pd.visible_to != "nobody"},
            "mechanics": [{"id": m.id, "name": m.name, "summary": m.summary, "creator": m.creator,
                           "creator_name": self.players[m.creator].name if m.creator in self.players
                           else m.creator, "day": m.day, "disabled": m.disabled}
                          for m in self.mechanics.values()],
        }


def _jsonable(v: Any) -> Any:
    if isinstance(v, (Player, Entity)):
        return getattr(v, "name", v.id)
    if isinstance(v, ZoneInstance):
        return v.zone
    if isinstance(v, WorldProxy):
        return "world"
    if isinstance(v, float):
        return tidy(round(v, 2))
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v

