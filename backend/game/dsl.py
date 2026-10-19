"""
Mechanics DSL v0.1 — schema for player-generated game mechanics.

A Mechanic is pure data. Agents emit it as JSON (structured output against
Mechanic.model_json_schema()), validate_mechanic() checks it against the live
game, and the engine interprets it. No player-authored code ever runs.

Units: positions/distances in tiles, time in seconds, one day = 600 s.

Evaluation context (roots usable in {"ref": ...} and as targets):
  actor    player who caused the event (status hooks: the holder)
  target   entity/player acted upon (interact, collide, ability target...)
  it       current item inside for_each / Selector.where / Aggregate.value
  event    event payload (event.amount, event.from, custom emit data...)
  world    global props (world.day, world.time, world.<world_prop>)
  mechanic this mechanic's world-scoped props
  creator  the player who created this mechanic
  vars     values bound with the `let` effect
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# --------------------------------------------------------------------------- #
# Global constants (tune per game)
# --------------------------------------------------------------------------- #
DAY_SECONDS = 600
POINTS_CAP_PER_MECHANIC = 5          # max points one mechanic can give a player per day
MAX_ENTITIES_PER_TYPE = 200
MAX_SELECTION = 50                   # bounds every for_each / aggregate
MAX_NODES = 400                      # complexity budget per mechanic
MAX_EFFECTS = 20                     # per effect list

PLAYER_BUILTINS = {"points", "name", "hp", "max_hp", "team", "speed", "size",
                   "vision", "x", "y", "id"}
ENTITY_BUILTINS = {"hp", "owner", "x", "y", "type", "id", "age"}
WORLD_BUILTINS = {"day", "time", "time_left", "player_count", "phase"}


class S(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Text = Annotated[str, Field(max_length=280)]
Seconds = Annotated[float, Field(ge=0, le=DAY_SECONDS)]

# --------------------------------------------------------------------------- #
# Expressions
# --------------------------------------------------------------------------- #
REF_PATTERN = r"^(actor|target|it|event|world|mechanic|creator|vars)(\.[a-z][a-z0-9_]*)*$"


class Ref(S):
    """Read a value: "actor.points", "target.apples", "world.day", "event.from", "it.hp"."""
    ref: Annotated[str, Field(pattern=REF_PATTERN)]


OpName = Literal[
    # arithmetic
    "add", "sub", "mul", "div", "mod", "pow", "neg", "abs", "min", "max",
    "floor", "ceil", "round", "clamp",
    # logic / comparison
    "eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not", "if",
    # randomness
    "random", "random_int", "chance", "pick",
    # spatial — args are refs/positions and ids
    "distance", "angle_to", "in_zone", "on_tile", "facing",
    # status / inventory — (who, id)
    "has_status", "status_stacks", "has_ability", "has_item", "item_count",
    # strings
    "concat", "len", "contains", "lower",
    # game
    "time", "time_left", "day", "rank", "player_count", "is_creator", "is_owner",
]


class Op(S):
    """Function call. {"op": "gt", "args": [{"ref": "target.apples"}, 0]}"""
    op: OpName
    args: list["Expr"] = Field(default_factory=list, max_length=8)


class Aggregate(S):
    """Fold over a selection; `value` is evaluated per item with `it` bound."""
    agg: Literal["count", "sum", "min", "max", "avg", "any", "all"]
    of: "Selector"
    value: "Expr | None" = None


Expr = Union[bool, int, float, str, Ref, Op, Aggregate]

# --------------------------------------------------------------------------- #
# Targets, selectors, positions
# --------------------------------------------------------------------------- #
TargetName = Literal["actor", "target", "it", "creator", "everyone", "others",
                     "leader", "last_place", "random_player", "world", "mechanic"]


class Selector(S):
    """Query players, entities or zones. Always bounded by `limit`."""
    kind: Literal["players", "entities", "zones"] = "entities"
    types: list[Id] | None = None            # entity types or zone ids
    tags: list[Id] | None = None
    owner: TargetName | None = None
    near: "Position | None" = None
    radius: Expr | None = None
    where: Expr | None = None                # `it` bound to each candidate
    exclude_actor: bool = False
    sort_by: Expr | None = None
    descending: bool = False
    limit: Annotated[int, Field(ge=1, le=MAX_SELECTION)] = MAX_SELECTION


Target = Union[TargetName, Ref, Selector]


class Point(S):
    x: Expr
    y: Expr


class Around(S):
    """A position relative to something: random spot within radius, plus offset."""
    around: Target
    radius: Expr = 0
    offset: Point | None = None


PositionName = Literal["actor", "target", "it", "event", "creator",
                       "random", "random_empty", "center", "edge"]
Position = Union[PositionName, Point, Around]

# --------------------------------------------------------------------------- #
# Visuals
# --------------------------------------------------------------------------- #
Shape = Literal["circle", "square", "triangle", "diamond", "hexagon", "star",
                "ring", "cross", "none"]
Animation = Literal["none", "pulse", "spin", "bob", "shake", "blink"]
Layer = Literal["ground", "object", "overhead"]


class Visual(S):
    shape: Shape = "circle"
    color: Color = "#222222"
    emoji: Annotated[str, Field(max_length=8)] | None = None
    size: Annotated[float, Field(ge=0.25, le=8)] = 1.0
    label: Annotated[str, Field(max_length=24)] | None = None
    opacity: Annotated[float, Field(ge=0, le=1)] = 1.0
    outline: Color | None = None
    glow: bool = False
    animation: Animation = "none"
    layer: Layer = "object"


class VisualPatch(S):
    """Partial Visual for set_visual."""
    shape: Shape | None = None
    color: Color | None = None
    emoji: Annotated[str, Field(max_length=8)] | None = None
    size: Annotated[float, Field(ge=0.25, le=8)] | None = None
    label: Annotated[str, Field(max_length=24)] | None = None
    opacity: Annotated[float, Field(ge=0, le=1)] | None = None
    glow: bool | None = None
    animation: Animation | None = None

# --------------------------------------------------------------------------- #
# Definitions: properties, items, entities, terrain, zones, statuses, abilities
# --------------------------------------------------------------------------- #
class PropertyDef(S):
    """A named value on a player, entity or the world. Resources are player props."""
    name: Id
    type: Literal["number", "bool", "string", "player", "entity"] = "number"
    default: bool | int | float | str | None = 0
    min: float | None = None
    max: float | None = None
    visible_to: Literal["nobody", "owner", "everyone"] = "owner"
    label: Annotated[str, Field(max_length=24)] | None = None
    emoji: Annotated[str, Field(max_length=8)] | None = None


class ItemDef(S):
    item: Id
    name: Annotated[str, Field(max_length=32)]
    visual: Visual = Field(default_factory=Visual)
    stack_max: Annotated[int, Field(ge=1, le=999)] = 99
    tradable: bool = True
    droppable: bool = True
    usable: bool = False                     # fires `use_item`
    consumed_on_use: bool = True


class SpawnRule(S):
    count: Annotated[int, Field(ge=0, le=MAX_ENTITIES_PER_TYPE)] = 0
    at: Position = "random_empty"
    pattern: Literal["scatter", "cluster", "grid", "ring", "line"] = "scatter"
    radius: float | None = None
    respawn_every_s: Annotated[float, Field(ge=1)] | None = None
    respawn_up_to: int | None = None


class Behavior(S):
    """Built-in movement AI for entities."""
    kind: Literal["static", "wander", "follow", "flee", "chase", "patrol", "orbit",
                  "projectile", "bounce", "guard", "move_to"] = "static"
    target: Target | None = None             # who to follow/flee/chase/orbit
    speed: Expr = 1
    range: Expr | None = None                # aggro / guard / orbit radius
    path: list[Point] | None = None          # patrol waypoints
    stop_at: Position | None = None          # move_to destination


class EntityType(S):
    type: Id
    name: Annotated[str, Field(max_length=32)]
    visual: Visual = Field(default_factory=Visual)
    tags: list[Id] = Field(default_factory=list)
    solid: bool = False
    pushable: bool = False
    collectible: bool = False                # walk over → `collect`, then destroyed
    interactable_by: Literal["nobody", "anyone", "owner", "not_owner", "creator"] = "anyone"
    interact_range: Annotated[float, Field(ge=0.5, le=10)] = 1.5
    hp: Expr | None = None                   # set → can be damaged/defeated
    properties: list[PropertyDef] = Field(default_factory=list)
    behavior: Behavior = Field(default_factory=Behavior)
    lifetime_s: Expr | None = None
    spawn: SpawnRule | None = None
    max_alive: Annotated[int, Field(ge=1, le=MAX_ENTITIES_PER_TYPE)] = 50


class TileDef(S):
    """Ground paint. Placed with the `paint` effect."""
    tile: Id
    color: Color
    walkable: bool = True
    speed_mult: Annotated[float, Field(ge=0, le=3)] = 1.0
    blocks_vision: bool = False


class ZoneDef(S):
    """An area with passive properties; fires enter_zone / leave_zone."""
    zone: Id
    name: Annotated[str, Field(max_length=32)]
    shape: Literal["circle", "rect"] = "circle"
    at: Position = "center"
    radius: float | None = 3
    width: float | None = None
    height: float | None = None
    visual: Visual = Field(default_factory=lambda: Visual(opacity=0.3, layer="ground"))
    speed_mult: Annotated[float, Field(ge=0, le=3)] = 1.0
    no_interact: bool = False
    no_abilities: bool = False
    hides_players: bool = False
    blocks: Literal["nobody", "everyone", "others", "non_owner"] = "nobody"
    properties: list[PropertyDef] = Field(default_factory=list)
    placed_at_start: bool = True             # False → only via create_zone


Stat = Literal["speed", "size", "vision", "interact_range", "points_mult",
               "item_gain_mult", "damage_mult", "defense_mult", "cooldown_mult"]
Flag = Literal["frozen", "invisible", "silenced", "disarmed", "immune", "ghost",
               "marked", "reversed_controls"]


class Modifier(S):
    stat: Stat
    op: Literal["add", "mul", "set"] = "mul"
    value: Expr


class StatusDef(S):
    """Temporary buff/debuff on a player or entity. In hooks, `actor` = holder."""
    status: Id
    name: Annotated[str, Field(max_length=32)]
    visual: Visual | None = None             # overlay
    duration_s: Expr = 10
    stacking: Literal["refresh", "stack", "extend", "ignore"] = "refresh"
    max_stacks: Annotated[int, Field(ge=1, le=10)] = 1
    modifiers: list[Modifier] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    transfers_on_touch: bool = False         # hot-potato style
    transfer_cooldown_s: Seconds = 3
    tick_s: Annotated[float, Field(ge=0.5)] | None = None
    on_apply: list["Effect"] = Field(default_factory=list, max_length=MAX_EFFECTS)
    on_tick: list["Effect"] = Field(default_factory=list, max_length=MAX_EFFECTS)
    on_expire: list["Effect"] = Field(default_factory=list, max_length=MAX_EFFECTS)


class Limits(S):
    """Rate limits. Every player-triggered rule and ability needs at least one."""
    cooldown_s: Seconds | None = None                # per player
    global_cooldown_s: Seconds | None = None
    max_per_player_per_day: Annotated[int, Field(ge=1)] | None = None
    max_per_day: Annotated[int, Field(ge=1)] | None = None
    max_total: Annotated[int, Field(ge=1)] | None = None

    def is_set(self) -> bool:
        return any(v is not None for v in self.model_dump().values())


class Cost(S):
    what: Id                                  # item id or numeric player prop (incl. "points")
    amount: Expr = 1


class AbilityDef(S):
    """A player action bound to a key by the client."""
    ability: Id
    name: Annotated[str, Field(max_length=32)]
    description: Text = ""
    icon: Annotated[str, Field(max_length=8)] | None = None
    available_to: Literal["everyone", "creator", "granted"] = "everyone"
    targeting: Literal["none", "self", "direction", "point", "entity", "player"] = "none"
    range: Expr = 3
    cost: list[Cost] = Field(default_factory=list)
    if_: list[Expr] = Field(default_factory=list, alias="if", max_length=8)
    do: list["Effect"] = Field(min_length=1, max_length=MAX_EFFECTS)
    limits: Limits = Field(default_factory=Limits)
    disabled: bool = False

# --------------------------------------------------------------------------- #
# Effects (discriminated by "fx")
# --------------------------------------------------------------------------- #
EffectList = Annotated[list["Effect"], Field(default_factory=list, max_length=MAX_EFFECTS)]


class SetFx(S):
    fx: Literal["set"]
    target: Target = "actor"
    prop: Id
    value: Expr


class ChangeFx(S):
    fx: Literal["change"]
    target: Target = "actor"
    prop: Id
    delta: Expr


class AddPointsFx(S):
    """The only way to change points. Capped at runtime per mechanic/player/day."""
    fx: Literal["add_points"]
    to: Target = "actor"
    amount: Expr
    reason: Text | None = None


class GiveFx(S):
    """Give (or take, if negative) items."""
    fx: Literal["give"]
    to: Target = "actor"
    item: Id
    amount: Expr = 1


class TransferFx(S):
    fx: Literal["transfer"]
    from_: Target = Field(alias="from")
    to: Target
    what: Id                                  # item or numeric prop
    amount: Expr


class SpawnFx(S):
    fx: Literal["spawn"]
    type: Id
    at: Position = "actor"
    count: Expr = 1
    scatter: Expr = 0
    owner: Target | None = None
    props: dict[str, Expr] = Field(default_factory=dict)


class DestroyFx(S):
    fx: Literal["destroy"]
    target: Target = "target"


class MoveFx(S):
    fx: Literal["move"]
    target: Target = "actor"
    mode: Literal["teleport", "push", "pull", "dash", "walk_to", "swap"] = "teleport"
    to: Position | None = None
    distance: Expr = 3
    speed: Expr = 8


class DamageFx(S):
    """Reduce hp (negative heals). hp <= 0 fires `defeated`."""
    fx: Literal["damage"]
    target: Target = "target"
    amount: Expr


class ApplyStatusFx(S):
    fx: Literal["apply_status"]
    target: Target = "actor"
    status: Id
    duration_s: Expr | None = None
    stacks: Expr = 1


class RemoveStatusFx(S):
    fx: Literal["remove_status"]
    target: Target = "actor"
    status: Id


class GrantAbilityFx(S):
    fx: Literal["grant_ability"]
    target: Target = "actor"
    ability: Id
    uses: Annotated[int, Field(ge=1)] | None = None


class RevokeAbilityFx(S):
    fx: Literal["revoke_ability"]
    target: Target = "actor"
    ability: Id


class CreateZoneFx(S):
    fx: Literal["create_zone"]
    zone: Id
    at: Position = "actor"
    duration_s: Expr | None = None
    owner: Target | None = None


class RemoveZoneFx(S):
    fx: Literal["remove_zone"]
    zone: Id


class PaintFx(S):
    fx: Literal["paint"]
    tile: Id
    at: Position = "actor"
    radius: Expr = 1
    duration_s: Expr | None = None


class SetVisualFx(S):
    fx: Literal["set_visual"]
    target: Target = "actor"
    visual: VisualPatch
    duration_s: Expr | None = None


class EmitFx(S):
    """Fire a custom event (trigger: {on: custom, event: ...})."""
    fx: Literal["emit"]
    event: Id
    data: dict[str, Expr] = Field(default_factory=dict)


class AfterFx(S):
    fx: Literal["after"]
    delay_s: Seconds
    do: EffectList


class RepeatFx(S):
    fx: Literal["repeat"]
    every_s: Annotated[float, Field(ge=0.5, le=DAY_SECONDS)]
    times: Annotated[int, Field(ge=1, le=20)]
    do: EffectList


class IfFx(S):
    fx: Literal["if"]
    cond: Expr
    then: EffectList
    else_: EffectList = Field(alias="else")


class ForEachFx(S):
    fx: Literal["for_each"]
    in_: Selector = Field(alias="in")
    do: EffectList


class ChanceFx(S):
    fx: Literal["chance"]
    p: Expr
    do: EffectList
    else_: EffectList = Field(alias="else")


class Weighted(S):
    weight: Annotated[float, Field(gt=0)] = 1
    do: EffectList


class PickOneFx(S):
    fx: Literal["pick_one"]
    options: Annotated[list[Weighted], Field(min_length=2, max_length=8)]


class LetFx(S):
    """Bind vars.<name> for the remaining effects of this list."""
    fx: Literal["let"]
    name: Id
    value: Expr


class MessageFx(S):
    """Text supports templates: "{actor.name} found {event.amount} gold"."""
    fx: Literal["message"]
    to: Target = "actor"
    text: Text
    style: Literal["toast", "announce", "bubble", "log"] = "toast"


class VisualEffectFx(S):
    fx: Literal["fx"]
    at: Position = "actor"
    kind: Literal["burst", "ring", "flash", "shake", "trail", "confetti", "sound"]
    color: Color | None = None


class ChoiceOption(S):
    label: Annotated[str, Field(max_length=40)]
    do: EffectList


class PromptFx(S):
    """Ask a player to choose. Unanswered → `default` option (or nothing)."""
    fx: Literal["prompt"]
    to: Target = "actor"
    question: Text
    options: Annotated[list[ChoiceOption], Field(min_length=2, max_length=4)]
    timeout_s: Annotated[float, Field(ge=3, le=60)] = 15
    default: int | None = None


class VoteFx(S):
    """Players vote; the winning option's effects run (actor = each voter for that option)."""
    fx: Literal["vote"]
    voters: Target = "everyone"
    question: Text
    options: Annotated[list[ChoiceOption], Field(min_length=2, max_length=6)]
    duration_s: Annotated[float, Field(ge=10, le=120)] = 30
    tie: Literal["none", "random", "first"] = "random"


class StopFx(S):
    """Stop executing the current rule."""
    fx: Literal["stop"]


Effect = Annotated[
    Union[SetFx, ChangeFx, AddPointsFx, GiveFx, TransferFx, SpawnFx, DestroyFx,
          MoveFx, DamageFx, ApplyStatusFx, RemoveStatusFx, GrantAbilityFx,
          RevokeAbilityFx, CreateZoneFx, RemoveZoneFx, PaintFx, SetVisualFx,
          EmitFx, AfterFx, RepeatFx, IfFx, ForEachFx, ChanceFx, PickOneFx,
          LetFx, MessageFx, VisualEffectFx, PromptFx, VoteFx, StopFx],
    Field(discriminator="fx"),
]

# --------------------------------------------------------------------------- #
# Triggers & rules
# --------------------------------------------------------------------------- #
TriggerKind = Literal[
    "game_start", "day_start", "day_end", "every",
    "interact", "collide", "collect", "near", "enter_zone", "leave_zone",
    "use_ability", "use_item", "spawn", "destroy", "damaged", "defeated",
    "status_applied", "status_expired", "status_transferred",
    "property_changed", "points_changed", "item_gained",
    "chat", "idle", "custom",
]
SELF_PACED = {"game_start", "day_start", "day_end", "every"}


class Trigger(S):
    on: TriggerKind
    entity: Id | None = None                  # entity type filter
    tag: Id | None = None
    zone: Id | None = None
    ability: Id | None = None
    item: Id | None = None
    status: Id | None = None
    property: Id | None = None
    event: Id | None = None                   # custom
    every_s: Annotated[float, Field(ge=1, le=DAY_SECONDS)] | None = None
    radius: Annotated[float, Field(gt=0, le=20)] | None = None
    idle_s: Annotated[float, Field(ge=1)] | None = None
    chat_contains: Annotated[str, Field(max_length=40)] | None = None
    actor: Literal["anyone", "creator", "not_creator", "owner", "not_owner"] = "anyone"

    @model_validator(mode="after")
    def _required(self):
        need = {"every": "every_s", "near": "radius", "custom": "event",
                "enter_zone": "zone", "leave_zone": "zone", "idle": "idle_s",
                "use_ability": "ability", "use_item": "item"}
        f = need.get(self.on)
        if f and getattr(self, f) is None:
            raise ValueError(f"trigger '{self.on}' requires '{f}'")
        return self


class Rule(S):
    id: Id
    trigger: Trigger
    if_: list[Expr] = Field(default_factory=list, alias="if", max_length=8)
    do: list[Effect] = Field(min_length=1, max_length=MAX_EFFECTS)
    else_: EffectList = Field(alias="else")
    limits: Limits = Field(default_factory=Limits)
    priority: int = 0
    disabled: bool = False

# --------------------------------------------------------------------------- #
# HUD, patches, mechanic
# --------------------------------------------------------------------------- #
class HudElement(S):
    kind: Literal["counter", "bar", "timer", "text", "leaderboard", "icon"]
    label: Annotated[str, Field(max_length=24)]
    value: Expr
    max: Expr | None = None
    visible_to: Literal["owner", "everyone"] = "owner"
    anchor: Literal["top_left", "top", "top_right",
                    "bottom_left", "bottom", "bottom_right"] = "top_left"


class Patch(S):
    """Modify an earlier mechanic. Path is a JSON pointer; list items can be
    addressed by index or by their id/type/name, e.g. /rules/shake_tree/limits/cooldown_s."""
    mechanic: Id
    op: Literal["set", "scale", "add", "disable", "enable", "remove"]
    path: str = ""
    value: bool | int | float | str | None = None


class CreatorPerk(S):
    royalty_points: Annotated[float, Field(ge=0, le=1)] = 0   # per use by others
    exclusive_s: Annotated[float, Field(ge=0, le=120)] = 0    # creator-only window each day
    discount: Annotated[float, Field(ge=0, le=1)] = 0         # on ability costs


class MechanicLimits(S):
    points_per_player_per_day: Annotated[float, Field(ge=0, le=POINTS_CAP_PER_MECHANIC)] = POINTS_CAP_PER_MECHANIC
    points_loss_per_player_per_day: Annotated[float, Field(ge=0, le=POINTS_CAP_PER_MECHANIC)] = POINTS_CAP_PER_MECHANIC
    expires_after_days: Annotated[int, Field(ge=1)] | None = None


class Mechanic(S):
    """One player's mechanic for one day."""
    dsl_version: Literal["0.1"] = "0.1"
    id: Id
    name: Annotated[str, Field(max_length=40)]
    summary: Text                              # shown in patch notes
    creator: str
    day: Annotated[int, Field(ge=0)]
    player_props: list[PropertyDef] = Field(default_factory=list)
    world_props: list[PropertyDef] = Field(default_factory=list)
    items: list[ItemDef] = Field(default_factory=list)
    entities: list[EntityType] = Field(default_factory=list)
    tiles: list[TileDef] = Field(default_factory=list)
    zones: list[ZoneDef] = Field(default_factory=list)
    statuses: list[StatusDef] = Field(default_factory=list)
    abilities: list[AbilityDef] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    hud: list[HudElement] = Field(default_factory=list)
    patches: list[Patch] = Field(default_factory=list)
    creator_perk: CreatorPerk = Field(default_factory=CreatorPerk)
    limits: MechanicLimits = Field(default_factory=MechanicLimits)
    disabled: bool = False

    @model_validator(mode="after")
    def _structural(self):
        for r in self.rules:
            if r.trigger.on not in SELF_PACED and not r.limits.is_set():
                raise ValueError(f"rule '{r.id}' is player-triggered and needs limits")
        for a in self.abilities:
            if not (a.limits.is_set() or a.cost):
                raise ValueError(f"ability '{a.ability}' needs limits or a cost")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids")
        return self


for _m in [Op, Aggregate, Selector, Around, Behavior, EntityType, ZoneDef,
           StatusDef, AbilityDef, Rule, Mechanic, SpawnRule,
           *Effect.__origin__.__args__, Weighted, ChoiceOption]:
    _m.model_rebuild()

# --------------------------------------------------------------------------- #
# Semantic validation against the live game
# --------------------------------------------------------------------------- #
def _walk(node: Any):
    yield node
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            yield from _walk(getattr(node, name))
    elif isinstance(node, (list, tuple)):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        for x in node.values():
            yield from _walk(x)


def _registry(mechanics: list[Mechanic]) -> dict[str, set[str]]:
    reg = {k: set() for k in ("entity", "item", "status", "ability", "zone",
                              "tile", "prop", "event")}
    reg["prop"] |= PLAYER_BUILTINS | ENTITY_BUILTINS | WORLD_BUILTINS
    for m in mechanics:
        reg["entity"] |= {e.type for e in m.entities}
        reg["item"] |= {i.item for i in m.items}
        reg["status"] |= {s.status for s in m.statuses}
        reg["ability"] |= {a.ability for a in m.abilities}
        reg["zone"] |= {z.zone for z in m.zones}
        reg["tile"] |= {t.tile for t in m.tiles}
        reg["prop"] |= {p.name for p in m.player_props + m.world_props}
        for e in m.entities:
            reg["prop"] |= {p.name for p in e.properties}
        for z in m.zones:
            reg["prop"] |= {p.name for p in z.properties}
        reg["event"] |= {n.event for n in _walk(m) if isinstance(n, EmitFx)}
    return reg


def _own_ids(m: Mechanic) -> dict[str, set[str]]:
    return _registry([m]) | {"prop": {p.name for p in m.player_props + m.world_props}}


def apply_patch(target: Mechanic, p: Patch) -> Mechanic:
    data = target.model_dump(by_alias=True)
    keys = ("id", "type", "item", "ability", "status", "zone", "tile", "name")
    parts = [s for s in p.path.strip("/").split("/") if s]
    if parts and parts[0] in ("id", "creator", "creator_perk", "limits", "dsl_version"):
        raise ValueError(f"path '{p.path}' is protected")

    def step(node, seg):
        if isinstance(node, list):
            if seg.isdigit():
                return int(seg)
            for i, x in enumerate(node):
                if isinstance(x, dict) and any(x.get(k) == seg for k in keys):
                    return i
            raise KeyError(seg)
        if seg not in node:
            raise KeyError(seg)
        return seg

    parent, node, key = None, data, None
    for seg in parts:
        key = step(node, seg)
        parent, node = node, node[key]

    if p.op in ("disable", "enable"):
        if not isinstance(node, dict) or "disabled" not in node:
            raise ValueError("disable/enable must point at the mechanic, a rule or an ability")
        node["disabled"] = p.op == "disable"
    elif parent is None:
        raise ValueError(f"op '{p.op}' needs a path")
    elif p.op == "set":
        parent[key] = p.value
    elif p.op in ("scale", "add"):
        if not isinstance(node, (int, float)) or isinstance(node, bool):
            raise ValueError(f"'{p.op}' needs a numeric field")
        parent[key] = node * p.value if p.op == "scale" else node + p.value
    elif p.op == "remove":
        if not isinstance(parent, list):
            raise ValueError("remove must point at a list item")
        parent.pop(key)
    return Mechanic.model_validate(data)


def validate_mechanic(m: Mechanic, existing: list[Mechanic]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block deployment."""
    errors, warnings = [], []
    by_id = {x.id: x for x in existing}
    if m.id in by_id:
        errors.append(f"mechanic id '{m.id}' already exists")

    # namespace collisions
    old = _registry(existing)
    for kind, ids in _own_ids(m).items():
        if kind == "event":
            continue
        for clash in ids & (old[kind] - (PLAYER_BUILTINS | ENTITY_BUILTINS | WORLD_BUILTINS)):
            errors.append(f"{kind} '{clash}' already defined by another mechanic")

    reg = _registry(existing + [m])

    def need(kind: str, val: str | None, where: str):
        if val is not None and val not in reg[kind]:
            errors.append(f"{where}: unknown {kind} '{val}'")

    nodes = 0
    for n in _walk(m):
        if not isinstance(n, BaseModel):
            continue
        nodes += 1
        t = type(n).__name__
        if isinstance(n, Trigger):
            need("entity", n.entity, "trigger")
            need("item", n.item, "trigger")
            need("ability", n.ability, "trigger")
            need("status", n.status, "trigger")
            need("zone", n.zone, "trigger")
            need("prop", n.property, "trigger")
            if n.on == "custom" and n.event not in reg["event"]:
                warnings.append(f"custom event '{n.event}' is never emitted")
        elif isinstance(n, Selector):
            for ty in n.types or []:
                need("zone" if n.kind == "zones" else "entity", ty, "selector")
        elif isinstance(n, SpawnFx):
            need("entity", n.type, t)
        elif isinstance(n, GiveFx):
            need("item", n.item, t)
        elif isinstance(n, (Cost, TransferFx)):
            if n.what not in reg["item"] | reg["prop"]:
                errors.append(f"{t}: unknown item/prop '{n.what}'")
        elif isinstance(n, (ApplyStatusFx, RemoveStatusFx)):
            need("status", n.status, t)
        elif isinstance(n, (GrantAbilityFx, RevokeAbilityFx)):
            need("ability", n.ability, t)
        elif isinstance(n, (CreateZoneFx, RemoveZoneFx)):
            need("zone", n.zone, t)
        elif isinstance(n, PaintFx):
            need("tile", n.tile, t)
        elif isinstance(n, (SetFx, ChangeFx)):
            need("prop", n.prop, t)
        elif isinstance(n, AddPointsFx):
            if isinstance(n.amount, (int, float)) and abs(n.amount) > POINTS_CAP_PER_MECHANIC:
                errors.append(f"add_points {n.amount} exceeds cap {POINTS_CAP_PER_MECHANIC}")
        elif isinstance(n, Ref):
            root, *rest = n.ref.split(".")
            if rest and root not in ("event", "vars") and rest[0] not in reg["prop"]:
                errors.append(f"ref '{n.ref}': unknown prop '{rest[0]}'")
    if nodes > MAX_NODES:
        errors.append(f"too complex: {nodes} nodes > {MAX_NODES}")

    for p in m.patches:
        if p.mechanic not in by_id:
            errors.append(f"patch: unknown mechanic '{p.mechanic}'")
            continue
        try:
            by_id[p.mechanic] = apply_patch(by_id[p.mechanic], p)
        except (KeyError, ValueError, ValidationError) as e:
            errors.append(f"patch {p.op} {p.mechanic}{p.path}: {e}")
    return errors, warnings
