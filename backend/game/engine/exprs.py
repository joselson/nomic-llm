"""Expression evaluator (SPEC §4.5). Never raises on bad data: coerces and logs once."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from game.dsl import Aggregate, Mechanic, Op, Ref

from .state import Entity, Player, WorldProxy, ZoneInstance, pos_of

if TYPE_CHECKING:
    from .world import World

MAX_ABS = 1e9


class Ctx:
    """Evaluation context: actor, target, it, event, mechanic, vars (+ control flow)."""
    __slots__ = ("mechanic", "actor", "target", "it", "event", "vars", "depth", "flow")

    def __init__(self, mechanic: Mechanic | None = None, actor: Any = None, target: Any = None,
                 it: Any = None, event: dict | None = None, vars: dict | None = None,
                 depth: int = 0, flow: dict | None = None) -> None:
        self.mechanic = mechanic
        self.actor = actor
        self.target = target
        self.it = it
        self.event = event if event is not None else {}
        self.vars = vars if vars is not None else {}
        self.depth = depth
        self.flow = flow if flow is not None else {"stop": False}

    def with_(self, **kw) -> "Ctx":
        c = Ctx(self.mechanic, self.actor, self.target, self.it, self.event, self.vars,
                self.depth, self.flow)
        for k, v in kw.items():
            setattr(c, k, v)
        return c


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #
def num(v: Any) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        if v != v or v in (math.inf, -math.inf):
            return 0.0
        return max(-MAX_ABS, min(MAX_ABS, float(v)))
    if isinstance(v, str):
        try:
            return num(float(v))
        except ValueError:
            return 0.0
    return 0.0


def truthy(v: Any) -> bool:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0
    return bool(v)


def norm(v: Any) -> Any:
    """Normalize for equality: objects -> id, numbers -> float."""
    if isinstance(v, (Player, Entity, ZoneInstance)):
        return v.id
    if isinstance(v, WorldProxy):
        return "world"
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def tidy(v: float) -> int | float:
    """Return ints for whole numbers (nicer messages / JSON)."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    return v


# --------------------------------------------------------------------------- #
# Refs
# --------------------------------------------------------------------------- #
def get_attr(world: "World", obj: Any, name: str) -> Any:
    if isinstance(obj, Player):
        if name in ("points", "hp", "max_hp", "x", "y", "id", "name", "team", "facing", "color"):
            v = getattr(obj, name)
            return tidy(v) if isinstance(v, float) else v
        if name in ("speed", "size", "vision"):
            return obj.stats.get(name, 0)
        if name in obj.props:
            return world.read_prop(name, obj.props[name])
        if name in world.items:
            return obj.items.get(name, 0)
        return world.prop_default(name)
    if isinstance(obj, Entity):
        if name in ("x", "y", "id", "type", "facing"):
            return getattr(obj, name)
        if name == "hp":
            return obj.hp if obj.hp is not None else 0
        if name == "max_hp":
            return obj.max_hp if obj.max_hp is not None else 0
        if name == "owner":
            return world.players.get(obj.owner) if obj.owner else None
        if name == "age":
            return world.clock - obj.born_at
        if name == "name":
            et = world.entity_types.get(obj.type)
            return et.name if et else obj.type
        if name in obj.props:
            return world.read_prop(name, obj.props[name])
        return world.prop_default(name)
    if isinstance(obj, ZoneInstance):
        if name in ("x", "y", "id", "zone"):
            return getattr(obj, name)
        if name == "owner":
            return world.players.get(obj.owner) if obj.owner else None
        if name in obj.props:
            return world.read_prop(name, obj.props[name])
        return world.prop_default(name)
    if isinstance(obj, WorldProxy):
        if name == "day":
            return world.day
        if name == "time":
            return round(world.time, 2)
        if name == "time_left":
            return round(world.time_left, 2)
        if name == "player_count":
            return len(world.active_players())
        if name == "phase":
            return world.phase
        if name in world.world_props:
            return world.read_prop(name, world.world_props[name])
        return world.prop_default(name)
    if isinstance(obj, dict):
        return obj.get(name, 0)
    return world.prop_default(name)


def resolve_ref(world: "World", path: str, ctx: Ctx) -> Any:
    root, *rest = path.split(".")
    if root == "vars":
        if not rest:
            return dict(ctx.vars)
        val = ctx.vars.get(rest[0], 0)
        rest = rest[1:]
    elif root == "event":
        if not rest:
            return ctx.event
        val = ctx.event.get(rest[0], 0)
        rest = rest[1:]
    elif root in ("world", "mechanic"):
        val = world.proxy
    elif root == "creator":
        val = world.players.get(ctx.mechanic.creator) if ctx.mechanic else None
    else:
        val = getattr(ctx, root, None)
    for seg in rest:
        if isinstance(val, str) and (val in world.players or val in world.entities):
            val = world.players.get(val) or world.entities.get(val)
        val = get_attr(world, val, seg)
    return val


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def evaluate(world: "World", expr: Any, ctx: Ctx) -> Any:
    if expr is None or isinstance(expr, (bool, int, float, str)):
        return expr
    if isinstance(expr, Ref):
        return resolve_ref(world, expr.ref, ctx)
    if isinstance(expr, Op):
        fn = OPS.get(expr.op)
        if fn is None:
            world.log_once(f"op:{expr.op}", f"unsupported op '{expr.op}'")
            return 0
        return fn(world, expr.args, ctx)
    if isinstance(expr, Aggregate):
        return aggregate(world, expr, ctx)
    if isinstance(expr, dict):          # tolerate raw dicts (e.g. from patches)
        if "ref" in expr:
            return resolve_ref(world, str(expr["ref"]), ctx)
        return 0
    return expr


def aggregate(world: "World", a: Aggregate, ctx: Ctx) -> Any:
    from .targets import select
    items = select(world, a.of, ctx)
    vals = [evaluate(world, a.value, ctx.with_(it=x)) if a.value is not None else x for x in items]
    if a.agg == "count":
        return len([v for v in vals if truthy(v)]) if a.value is not None else len(vals)
    if a.agg == "any":
        return any(truthy(v) for v in vals)
    if a.agg == "all":
        return all(truthy(v) for v in vals)
    ns = [num(v) for v in vals]
    if not ns:
        return 0
    if a.agg == "sum":
        return tidy(sum(ns))
    if a.agg == "min":
        return tidy(min(ns))
    if a.agg == "max":
        return tidy(max(ns))
    return sum(ns) / len(ns)


def as_obj(world: "World", v: Any) -> Any:
    if isinstance(v, str):
        return world.players.get(v) or world.entities.get(v)
    return v


def _ops() -> dict:
    def E(w, args, ctx, i):
        return evaluate(w, args[i], ctx) if i < len(args) else None

    def N(w, args, ctx, i):
        return num(E(w, args, ctx, i))

    def nums(w, args, ctx):
        return [num(evaluate(w, a, ctx)) for a in args]

    def div(a, b):
        return 0 if b == 0 else a / b

    def pw(a, b):
        try:
            r = math.pow(a, b)
        except (OverflowError, ValueError, ZeroDivisionError):
            return 0
        return num(r) if not isinstance(r, complex) else 0

    def sub(w, args, ctx):
        ns = nums(w, args, ctx)
        if not ns:
            return 0
        if len(ns) == 1:
            return tidy(-ns[0])
        return tidy(ns[0] - sum(ns[1:]))

    def cmp(f):
        return lambda w, a, c: f(N(w, a, c, 0), N(w, a, c, 1))

    def eq(w, a, c):
        x, y = norm(E(w, a, c, 0)), norm(E(w, a, c, 1))
        if isinstance(x, (bool, float)) and isinstance(y, (bool, float)):
            return num(x) == num(y)
        return x == y

    def and_(w, args, ctx):
        return all(truthy(evaluate(w, a, ctx)) for a in args)

    def or_(w, args, ctx):
        return any(truthy(evaluate(w, a, ctx)) for a in args)

    def if_(w, a, c):
        return E(w, a, c, 1) if truthy(E(w, a, c, 0)) else E(w, a, c, 2)

    def random_(w, a, c):
        if len(a) >= 2:
            lo, hi = N(w, a, c, 0), N(w, a, c, 1)
            return w.rng.uniform(min(lo, hi), max(lo, hi))
        return w.rng.random()

    def random_int(w, a, c):
        lo, hi = int(math.floor(N(w, a, c, 0))), int(math.floor(N(w, a, c, 1)))
        return w.rng.randint(min(lo, hi), max(lo, hi))

    def pick(w, a, c):
        return evaluate(w, w.rng.choice(a), c) if a else 0

    def obj(w, a, c, i):
        return as_obj(w, E(w, a, c, i))

    def distance(w, a, c):
        p, q = pos_of(obj(w, a, c, 0)), pos_of(obj(w, a, c, 1))
        if p is None or q is None:
            return 0
        return math.hypot(p[0] - q[0], p[1] - q[1])

    def angle_to(w, a, c):
        p, q = pos_of(obj(w, a, c, 0)), pos_of(obj(w, a, c, 1))
        if p is None or q is None:
            return 0
        return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))

    def in_zone(w, a, c):
        who, zid = obj(w, a, c, 0), E(w, a, c, 1)
        p = pos_of(who)
        return p is not None and w.in_zone(p[0], p[1], str(zid))

    def on_tile(w, a, c):
        p = pos_of(obj(w, a, c, 0))
        return p is not None and w.tiles.at(*p) == E(w, a, c, 1)

    def facing(w, a, c):
        who, other = obj(w, a, c, 0), obj(w, a, c, 1)
        p, q = pos_of(who), pos_of(other)
        if p is None or q is None or not hasattr(who, "facing"):
            return False
        ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
        d = (ang - who.facing + 180) % 360 - 180
        return abs(d) <= 60

    def has_status(w, a, c):
        who, sid = obj(w, a, c, 0), E(w, a, c, 1)
        return any(s.status == sid for s in getattr(who, "statuses", []))

    def status_stacks(w, a, c):
        who, sid = obj(w, a, c, 0), E(w, a, c, 1)
        return sum(s.stacks for s in getattr(who, "statuses", []) if s.status == sid)

    def has_ability(w, a, c):
        who, aid = obj(w, a, c, 0), E(w, a, c, 1)
        return isinstance(who, Player) and w.player_has_ability(who, str(aid))

    def item_count(w, a, c):
        who, iid = obj(w, a, c, 0), E(w, a, c, 1)
        return who.items.get(iid, 0) if isinstance(who, Player) else 0

    def to_s(v):
        if isinstance(v, (Player, Entity)):
            return getattr(v, "name", v.id)
        if isinstance(v, float):
            return str(tidy(round(v, 2)))
        return "" if v is None else str(v)

    def length(w, a, c):
        v = E(w, a, c, 0)
        return len(v) if isinstance(v, (str, list, tuple, dict)) else 0

    def contains(w, a, c):
        x, y = E(w, a, c, 0), E(w, a, c, 1)
        if isinstance(x, (list, tuple)):
            return norm(y) in [norm(v) for v in x]
        return to_s(y).lower() in to_s(x).lower()

    def rank(w, a, c):
        who = obj(w, a, c, 0)
        if not isinstance(who, Player):
            return 0
        return 1 + sum(1 for p in w.active_players() if p.points > who.points)

    def is_creator(w, a, c):
        who = obj(w, a, c, 0)
        return isinstance(who, Player) and c.mechanic is not None and who.id == c.mechanic.creator

    def is_owner(w, a, c):
        who, ent = obj(w, a, c, 0), obj(w, a, c, 1)
        return isinstance(who, Player) and getattr(ent, "owner", None) == who.id

    return {
        "add": lambda w, a, c: tidy(sum(nums(w, a, c))),
        "sub": sub,
        "mul": lambda w, a, c: tidy(math.prod(nums(w, a, c))) if a else 0,
        "div": lambda w, a, c: div(N(w, a, c, 0), N(w, a, c, 1)),
        "mod": lambda w, a, c: 0 if N(w, a, c, 1) == 0 else tidy(N(w, a, c, 0) % N(w, a, c, 1)),
        "pow": lambda w, a, c: pw(N(w, a, c, 0), N(w, a, c, 1)),
        "neg": lambda w, a, c: tidy(-N(w, a, c, 0)),
        "abs": lambda w, a, c: tidy(abs(N(w, a, c, 0))),
        "min": lambda w, a, c: tidy(min(nums(w, a, c))) if a else 0,
        "max": lambda w, a, c: tidy(max(nums(w, a, c))) if a else 0,
        "floor": lambda w, a, c: math.floor(N(w, a, c, 0)),
        "ceil": lambda w, a, c: math.ceil(N(w, a, c, 0)),
        "round": lambda w, a, c: round(N(w, a, c, 0)),
        "clamp": lambda w, a, c: tidy(max(N(w, a, c, 1), min(N(w, a, c, 2), N(w, a, c, 0)))),
        "eq": eq,
        "ne": lambda w, a, c: not eq(w, a, c),
        "lt": cmp(lambda x, y: x < y),
        "le": cmp(lambda x, y: x <= y),
        "gt": cmp(lambda x, y: x > y),
        "ge": cmp(lambda x, y: x >= y),
        "and": and_,
        "or": or_,
        "not": lambda w, a, c: not truthy(E(w, a, c, 0)),
        "if": if_,
        "random": random_,
        "random_int": random_int,
        "chance": lambda w, a, c: w.rng.random() < N(w, a, c, 0),
        "pick": pick,
        "distance": distance,
        "angle_to": angle_to,
        "in_zone": in_zone,
        "on_tile": on_tile,
        "facing": facing,
        "has_status": has_status,
        "status_stacks": status_stacks,
        "has_ability": has_ability,
        "has_item": lambda w, a, c: item_count(w, a, c) > 0,
        "item_count": item_count,
        "concat": lambda w, a, c: "".join(to_s(evaluate(w, x, c)) for x in a),
        "len": length,
        "contains": contains,
        "lower": lambda w, a, c: to_s(E(w, a, c, 0)).lower(),
        "time": lambda w, a, c: round(w.time, 2),
        "time_left": lambda w, a, c: round(w.time_left, 2),
        "day": lambda w, a, c: w.day,
        "rank": rank,
        "player_count": lambda w, a, c: len(w.active_players()),
        "is_creator": is_creator,
        "is_owner": is_owner,
    }


OPS = _ops()


def to_text(v: Any) -> str:
    if isinstance(v, (Player, Entity)):
        return v.name if isinstance(v, Player) else v.type
    if isinstance(v, float):
        return str(tidy(round(v, 2)))
    if isinstance(v, bool):
        return "yes" if v else "no"
    return "" if v is None else str(v)
