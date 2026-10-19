"""Effect executors, one per `fx` (SPEC §4.8)."""
from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any, Callable, Iterator

from game import dsl

from .events import Event
from .exprs import Ctx, evaluate, num, resolve_ref, to_text, truthy
from .state import Entity, Motion, Player, pos_of
from .targets import resolve_one, resolve_position, resolve_target, select

if TYPE_CHECKING:
    from .world import World

MAX_EFFECTS_PER_TICK = 20000
MAX_SPAWN_PER_EFFECT = 50


class EffectBudgetExceeded(RuntimeError):
    pass


def run_effects(world: "World", effects: list, ctx: Ctx) -> None:
    ctx = ctx.with_(vars=dict(ctx.vars))       # `let` scope = rest of this list
    for fx in effects:
        if ctx.flow["stop"]:
            return
        world.effects_this_tick += 1
        if world.effects_this_tick > MAX_EFFECTS_PER_TICK:
            raise EffectBudgetExceeded("too many effects in one tick")
        fn = EXECUTORS.get(type(fx))
        if fn is None:
            world.log_once(f"fx:{type(fx).__name__}", f"unsupported effect {type(fx).__name__}")
            continue
        fn(world, fx, ctx)


def each(world: "World", target: Any, ctx: Ctx) -> Iterator[tuple[Any, Ctx]]:
    objs, multi = resolve_target(world, target, ctx)
    for o in objs:
        if isinstance(o, Entity) and o.dead:
            continue
        yield o, (ctx.with_(it=o) if multi else ctx)


def ev_num(world: "World", expr: Any, ctx: Ctx) -> float:
    return num(evaluate(world, expr, ctx))


# --------------------------------------------------------------------------- #
# State changes
# --------------------------------------------------------------------------- #
def fx_set(w, fx: dsl.SetFx, ctx):
    for o, c in each(w, fx.target, ctx):
        w.set_prop(o, fx.prop, evaluate(w, fx.value, c), c)


def fx_change(w, fx: dsl.ChangeFx, ctx):
    for o, c in each(w, fx.target, ctx):
        cur = num(w.get_prop(o, fx.prop))
        w.set_prop(o, fx.prop, cur + ev_num(w, fx.delta, c), c)


def fx_add_points(w, fx: dsl.AddPointsFx, ctx):
    mid = ctx.mechanic.id if ctx.mechanic else "_"
    for o, c in each(w, fx.to, ctx):
        if isinstance(o, Player):
            reason = render_template(w, fx.reason, c) if fx.reason else None
            w.ledger.apply(mid, o, ev_num(w, fx.amount, c), reason)


def fx_give(w, fx: dsl.GiveFx, ctx):
    for o, c in each(w, fx.to, ctx):
        if isinstance(o, Player):
            w.give_item(o, fx.item, int(round(ev_num(w, fx.amount, c))), c)


def fx_transfer(w, fx: dsl.TransferFx, ctx):
    src, dst = resolve_one(w, fx.from_, ctx), resolve_one(w, fx.to, ctx)
    if src is None or dst is None or src is dst:
        return
    amount = max(0.0, ev_num(w, fx.amount, ctx))
    mid = ctx.mechanic.id if ctx.mechanic else "_"
    if fx.what == "points":
        if isinstance(src, Player) and isinstance(dst, Player):
            taken = -w.ledger.apply(mid, src, -min(amount, src.points), "transfer")
            if taken > 0:
                w.ledger.apply(mid, dst, taken, "transfer")
        return
    if fx.what in w.items:
        if isinstance(src, Player) and isinstance(dst, Player):
            n = min(int(amount), src.items.get(fx.what, 0))
            if n > 0:
                w.give_item(src, fx.what, -n, ctx)
                w.give_item(dst, fx.what, n, ctx)
        return
    have = num(w.get_prop(src, fx.what))
    n = min(amount, max(0.0, have))
    if n > 0:
        w.set_prop(src, fx.what, have - n, ctx)
        w.set_prop(dst, fx.what, num(w.get_prop(dst, fx.what)) + n, ctx)


def fx_spawn(w, fx: dsl.SpawnFx, ctx):
    count = int(max(0, min(MAX_SPAWN_PER_EFFECT, ev_num(w, fx.count, ctx))))
    scatter = max(0.0, ev_num(w, fx.scatter, ctx))
    owner = resolve_one(w, fx.owner, ctx) if fx.owner is not None else None
    for _ in range(count):
        p = resolve_position(w, fx.at, ctx) or w.random_empty()
        x, y = p
        if scatter > 0:
            a, d = w.rng.uniform(0, math.tau), scatter * math.sqrt(w.rng.random())
            x, y = x + math.cos(a) * d, y + math.sin(a) * d
        props = {k: evaluate(w, v, ctx) for k, v in fx.props.items()}
        e = w.spawn_entity(fx.type, x, y, owner=getattr(owner, "id", None), props=props,
                           ctx=ctx)
        if e is None:
            break


def fx_destroy(w, fx: dsl.DestroyFx, ctx):
    for o, _ in each(w, fx.target, ctx):
        if isinstance(o, Entity):
            w.destroy_entity(o, ctx.actor, ctx.depth)


def fx_move(w, fx: dsl.MoveFx, ctx):
    dist = max(0.0, min(float(w.map_size), ev_num(w, fx.distance, ctx)))
    speed = max(0.5, min(40.0, ev_num(w, fx.speed, ctx)))
    for o, c in each(w, fx.target, ctx):
        if not isinstance(o, (Player, Entity)):
            continue
        if fx.mode == "teleport":
            p = resolve_position(w, fx.to, c) if fx.to is not None else w.random_empty()
            if p:
                w.teleport(o, *p)
        elif fx.mode in ("push", "pull"):
            src = pos_of(ctx.actor)
            if src is None or o is ctx.actor:
                ang = math.radians(getattr(o, "facing", 0.0) + (180 if fx.mode == "push" else 0))
                dx, dy = math.cos(ang), math.sin(ang)
                gap = dist
            else:
                dx, dy = o.x - src[0], o.y - src[1]
                d = math.hypot(dx, dy)
                if d < 1e-6:
                    a = w.rng.uniform(0, math.tau)
                    dx, dy, d = math.cos(a), math.sin(a), 1.0
                dx, dy = dx / d, dy / d
                gap = dist
                if fx.mode == "pull":
                    dx, dy = -dx, -dy
                    gap = min(dist, max(0.0, d - 1.0))
            if gap > 0:
                o.motion = Motion(dx * speed, dy * speed, gap)
        elif fx.mode == "dash":
            direction = c.event.get("direction")
            if fx.to is not None:
                p = resolve_position(w, fx.to, c)
                ang = math.atan2(p[1] - o.y, p[0] - o.x) if p else math.radians(o.facing)
            elif isinstance(direction, (int, float)) and not isinstance(direction, bool):
                ang = math.radians(direction)
            else:
                ang = math.radians(o.facing)
            o.facing = math.degrees(ang)
            o.motion = Motion(math.cos(ang) * speed, math.sin(ang) * speed, dist)
        elif fx.mode == "walk_to":
            p = resolve_position(w, fx.to, c) if fx.to is not None else None
            if p:
                d = math.hypot(p[0] - o.x, p[1] - o.y)
                if d > 1e-3:
                    o.motion = Motion((p[0] - o.x) / d * speed, (p[1] - o.y) / d * speed, d, goal=p)
        elif fx.mode == "swap":
            other = ctx.actor if o is not ctx.actor else ctx.target
            if isinstance(other, (Player, Entity)) and other is not o:
                ox, oy = other.x, other.y
                w.teleport(other, o.x, o.y)
                w.teleport(o, ox, oy)


def fx_damage(w, fx: dsl.DamageFx, ctx):
    for o, c in each(w, fx.target, ctx):
        if isinstance(o, (Player, Entity)):
            w.damage(o, ev_num(w, fx.amount, c), ctx.actor, ctx.depth)


def fx_apply_status(w, fx: dsl.ApplyStatusFx, ctx):
    for o, c in each(w, fx.target, ctx):
        if isinstance(o, (Player, Entity)):
            dur = ev_num(w, fx.duration_s, c) if fx.duration_s is not None else None
            w.apply_status(o, fx.status, dur, int(max(1, ev_num(w, fx.stacks, c))), ctx.actor, ctx.depth)


def fx_remove_status(w, fx: dsl.RemoveStatusFx, ctx):
    for o, _ in each(w, fx.target, ctx):
        if isinstance(o, (Player, Entity)):
            w.remove_status(o, fx.status)


def fx_grant_ability(w, fx: dsl.GrantAbilityFx, ctx):
    for o, _ in each(w, fx.target, ctx):
        if isinstance(o, Player):
            cur = o.granted_abilities.get(fx.ability, 0)
            if fx.uses is None or cur is None:
                o.granted_abilities[fx.ability] = None
            else:
                o.granted_abilities[fx.ability] = cur + fx.uses


def fx_revoke_ability(w, fx: dsl.RevokeAbilityFx, ctx):
    for o, _ in each(w, fx.target, ctx):
        if isinstance(o, Player):
            o.granted_abilities.pop(fx.ability, None)


def fx_create_zone(w, fx: dsl.CreateZoneFx, ctx):
    p = resolve_position(w, fx.at, ctx)
    if p is None:
        return
    owner = resolve_one(w, fx.owner, ctx) if fx.owner is not None else None
    dur = ev_num(w, fx.duration_s, ctx) if fx.duration_s is not None else None
    w.create_zone(fx.zone, p[0], p[1], owner=getattr(owner, "id", None), duration=dur)


def fx_remove_zone(w, fx: dsl.RemoveZoneFx, ctx):
    w.remove_zones(fx.zone)


def fx_paint(w, fx: dsl.PaintFx, ctx):
    p = resolve_position(w, fx.at, ctx)
    if p is None:
        return
    r = max(0.0, min(8.0, ev_num(w, fx.radius, ctx)))
    dur = ev_num(w, fx.duration_s, ctx) if fx.duration_s is not None else None
    w.paint(fx.tile, p[0], p[1], r, dur)


def fx_set_visual(w, fx: dsl.SetVisualFx, ctx):
    patch = fx.visual.model_dump(exclude_none=True)
    dur = ev_num(w, fx.duration_s, ctx) if fx.duration_s is not None else None
    for o, _ in each(w, fx.target, ctx):
        if isinstance(o, (Player, Entity)):
            before = {k: o.visual.get(k) for k in patch}
            o.visual.update(patch)
            if dur:
                def revert(o=o, before=before, patch=patch):
                    for k, v in before.items():
                        if o.visual.get(k) == patch[k]:
                            if v is None:
                                o.visual.pop(k, None)
                            else:
                                o.visual[k] = v
                w.schedule(dur, revert)


def fx_emit(w, fx: dsl.EmitFx, ctx):
    data = {k: evaluate(w, v, ctx) for k, v in fx.data.items()}
    data["event"] = fx.event
    w.push(Event("custom", ctx.actor, ctx.target, data, ctx.depth + 1))


def fx_after(w, fx: dsl.AfterFx, ctx):
    c = ctx.with_(vars=dict(ctx.vars), flow={"stop": False})
    w.schedule(fx.delay_s, lambda: w.run(fx.do, c, key=f"{ctx.mechanic.id if ctx.mechanic else '_'}/after"))


def fx_repeat(w, fx: dsl.RepeatFx, ctx):
    for i in range(1, fx.times + 1):
        c = ctx.with_(vars=dict(ctx.vars), flow={"stop": False})
        w.schedule(fx.every_s * i, lambda c=c: w.run(fx.do, c, key=f"{ctx.mechanic.id if ctx.mechanic else '_'}/repeat"))


def fx_if(w, fx: dsl.IfFx, ctx):
    run_effects(w, fx.then if truthy(evaluate(w, fx.cond, ctx)) else fx.else_, ctx)


def fx_for_each(w, fx: dsl.ForEachFx, ctx):
    for o in select(w, fx.in_, ctx):
        if ctx.flow["stop"]:
            return
        if isinstance(o, Entity) and o.dead:
            continue
        run_effects(w, fx.do, ctx.with_(it=o))


def fx_chance(w, fx: dsl.ChanceFx, ctx):
    run_effects(w, fx.do if w.rng.random() < ev_num(w, fx.p, ctx) else fx.else_, ctx)


def fx_pick_one(w, fx: dsl.PickOneFx, ctx):
    opt = w.rng.choices(fx.options, weights=[o.weight for o in fx.options])[0]
    run_effects(w, opt.do, ctx)


def fx_let(w, fx: dsl.LetFx, ctx):
    ctx.vars[fx.name] = evaluate(w, fx.value, ctx)


TEMPLATE = re.compile(r"\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}")
ROOTS = {"actor", "target", "it", "event", "world", "mechanic", "creator", "vars"}


def render_template(w: "World", text: str, ctx: Ctx) -> str:
    def sub(m: re.Match) -> str:
        path = m.group(1)
        if path.split(".")[0] not in ROOTS:
            return m.group(0)
        return to_text(resolve_ref(w, path, ctx))
    return TEMPLATE.sub(sub, text)


def fx_message(w, fx: dsl.MessageFx, ctx):
    text = render_template(w, fx.text, ctx)
    frm = getattr(ctx.actor, "id", None) if fx.style == "bubble" else None
    if fx.to == "everyone":
        w.send(None, {"t": "message", "text": text, "style": fx.style, "from": frm})
        return
    pids = [o.id for o, _ in each(w, fx.to, ctx) if isinstance(o, Player)]
    if fx.style == "bubble" and frm is not None:
        pids = None   # speech bubbles are visible to everyone nearby
    if pids is None or pids:
        w.send(pids, {"t": "message", "text": text, "style": fx.style, "from": frm})


def fx_visual(w, fx: dsl.VisualEffectFx, ctx):
    p = resolve_position(w, fx.at, ctx)
    if p:
        w.send(None, {"t": "fx", "kind": fx.kind, "x": round(p[0], 2), "y": round(p[1], 2),
                      "color": fx.color})


def fx_prompt(w, fx: dsl.PromptFx, ctx):
    for o, c in each(w, fx.to, ctx):
        if isinstance(o, Player):
            w.open_prompt(o, render_template(w, fx.question, c), fx.options, fx.timeout_s, fx.default, c)


def fx_vote(w, fx: dsl.VoteFx, ctx):
    voters = [o for o, _ in each(w, fx.voters, ctx) if isinstance(o, Player)]
    if voters:
        w.open_vote(voters, render_template(w, fx.question, ctx), fx.options, fx.duration_s, fx.tie, ctx)


def fx_stop(w, fx: dsl.StopFx, ctx):
    ctx.flow["stop"] = True


EXECUTORS: dict[type, Callable[["World", Any, Ctx], None]] = {
    dsl.SetFx: fx_set, dsl.ChangeFx: fx_change, dsl.AddPointsFx: fx_add_points,
    dsl.GiveFx: fx_give, dsl.TransferFx: fx_transfer, dsl.SpawnFx: fx_spawn,
    dsl.DestroyFx: fx_destroy, dsl.MoveFx: fx_move, dsl.DamageFx: fx_damage,
    dsl.ApplyStatusFx: fx_apply_status, dsl.RemoveStatusFx: fx_remove_status,
    dsl.GrantAbilityFx: fx_grant_ability, dsl.RevokeAbilityFx: fx_revoke_ability,
    dsl.CreateZoneFx: fx_create_zone, dsl.RemoveZoneFx: fx_remove_zone, dsl.PaintFx: fx_paint,
    dsl.SetVisualFx: fx_set_visual, dsl.EmitFx: fx_emit, dsl.AfterFx: fx_after,
    dsl.RepeatFx: fx_repeat, dsl.IfFx: fx_if, dsl.ForEachFx: fx_for_each,
    dsl.ChanceFx: fx_chance, dsl.PickOneFx: fx_pick_one, dsl.LetFx: fx_let,
    dsl.MessageFx: fx_message, dsl.VisualEffectFx: fx_visual, dsl.PromptFx: fx_prompt,
    dsl.VoteFx: fx_vote, dsl.StopFx: fx_stop,
}

FX_NAMES = {cls.model_fields["fx"].annotation.__args__[0]: cls for cls in EXECUTORS}
