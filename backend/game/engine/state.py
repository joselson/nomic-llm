"""Runtime state objects. Plain mutable dataclasses; identity equality."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

PLAYER_RADIUS = 0.4
BASE_SPEED = 4.0
PLAYER_HP = 10
RESPAWN_S = 3.0
INPUT_DT = 0.05                       # each client input command = 50 ms of movement
PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231",
           "#911eb4", "#14a3c7", "#f032e6", "#9a6324"]

BASE_STATS = {"speed": BASE_SPEED, "size": 1.0, "vision": 12.0, "interact_range": 1.0,
              "points_mult": 1.0, "item_gain_mult": 1.0, "damage_mult": 1.0,
              "defense_mult": 1.0, "cooldown_mult": 1.0}


@dataclass(eq=False)
class StatusInstance:
    status: str
    mechanic_id: str
    stacks: int
    expires_at: float                 # world clock
    next_tick_at: float | None = None
    source: str | None = None         # applier id
    transferable_at: float = 0.0


@dataclass(eq=False)
class Motion:
    """A forced displacement in progress (dash / push / pull / walk_to)."""
    vx: float
    vy: float
    remaining: float                  # tiles left to travel
    goal: tuple[float, float] | None = None


@dataclass(eq=False)
class Player:
    id: str
    name: str
    color: str
    x: float = 0.0
    y: float = 0.0
    facing: float = 0.0               # degrees; 0 = east, 90 = south (screen coords)
    points: float = 0.0
    hp: float = PLAYER_HP
    max_hp: float = PLAYER_HP
    team: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    items: dict[str, int] = field(default_factory=dict)
    statuses: list[StatusInstance] = field(default_factory=list)
    granted_abilities: dict[str, int | None] = field(default_factory=dict)
    stats: dict[str, float] = field(default_factory=lambda: dict(BASE_STATS))
    flags: set[str] = field(default_factory=set)
    input_dx: float = 0.0
    input_dy: float = 0.0
    input_seq: int = 0                # last input command applied (acked to the client)
    input_queue: deque = field(default_factory=lambda: deque(maxlen=12))
    input_budget: float = 0.0
    last_active: float = 0.0          # world clock of last movement/action
    connected: bool = True
    defeated_until: float | None = None
    visual: dict[str, Any] = field(default_factory=dict)   # set_visual overrides
    motion: Motion | None = None

    kind = "player"

    @property
    def radius(self) -> float:
        return PLAYER_RADIUS * max(0.25, self.stats.get("size", 1.0))

    @property
    def alive(self) -> bool:
        return self.defeated_until is None


@dataclass(eq=False)
class Entity:
    id: str
    type: str
    mechanic_id: str
    x: float
    y: float
    size: float = 1.0                 # diameter in tiles (from visual.size)
    square: bool = False              # AABB collider instead of circle
    solid: bool = False
    vx: float = 0.0
    vy: float = 0.0
    props: dict[str, Any] = field(default_factory=dict)
    owner: str | None = None
    hp: float | None = None
    max_hp: float | None = None
    born_at: float = 0.0
    expires_at: float | None = None
    spawn_x: float = 0.0
    spawn_y: float = 0.0
    facing: float = 0.0
    brain: dict[str, Any] = field(default_factory=dict)   # behavior state
    statuses: list[StatusInstance] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=lambda: dict(BASE_STATS))
    flags: set[str] = field(default_factory=set)
    visual: dict[str, Any] = field(default_factory=dict)
    motion: Motion | None = None
    dead: bool = False

    kind = "entity"

    @property
    def radius(self) -> float:
        return self.size / 2 * max(0.25, self.stats.get("size", 1.0))


@dataclass(eq=False)
class ZoneInstance:
    id: str
    zone: str                         # ZoneDef id
    mechanic_id: str
    x: float
    y: float
    shape: str = "circle"
    radius: float = 3.0
    width: float = 6.0
    height: float = 6.0
    owner: str | None = None
    expires_at: float | None = None
    props: dict[str, Any] = field(default_factory=dict)

    kind = "zone"

    def contains(self, x: float, y: float) -> bool:
        if self.shape == "rect":
            return abs(x - self.x) <= self.width / 2 and abs(y - self.y) <= self.height / 2
        return math.hypot(x - self.x, y - self.y) <= self.radius


class TileMap:
    """Sparse ground paint: (tx, ty) -> (tile_id, expires_at|None)."""

    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], tuple[str, float | None]] = {}
        self.dirty: set[tuple[int, int]] = set()

    def paint(self, tx: int, ty: int, tile: str, expires_at: float | None) -> None:
        self.cells[(tx, ty)] = (tile, expires_at)
        self.dirty.add((tx, ty))

    def clear(self, tx: int, ty: int) -> None:
        if self.cells.pop((tx, ty), None) is not None:
            self.dirty.add((tx, ty))

    def at(self, x: float, y: float) -> str | None:
        c = self.cells.get((int(math.floor(x)), int(math.floor(y))))
        return c[0] if c else None

    def expire(self, now: float) -> None:
        for k, (_, exp) in list(self.cells.items()):
            if exp is not None and exp <= now:
                self.clear(*k)

    def take_delta(self) -> list[list]:
        out = [[tx, ty, self.cells[(tx, ty)][0] if (tx, ty) in self.cells else None]
               for tx, ty in self.dirty]
        self.dirty.clear()
        return out

    def full(self) -> list[list]:
        return [[tx, ty, t] for (tx, ty), (t, _) in self.cells.items()]


class WorldProxy:
    """The `world` / `mechanic` root and target: global props + clock builtins."""
    kind = "world"
    id = "world"

    def __init__(self, world) -> None:
        self.w = world


Obj = Player | Entity | ZoneInstance | WorldProxy


def pos_of(o: Any) -> tuple[float, float] | None:
    if isinstance(o, (Player, Entity, ZoneInstance)):
        return (o.x, o.y)
    if isinstance(o, dict) and "x" in o and "y" in o:
        try:
            return (float(o["x"]), float(o["y"]))
        except (TypeError, ValueError):
            return None
    if isinstance(o, (tuple, list)) and len(o) == 2 and all(isinstance(v, (int, float)) for v in o):
        return (float(o[0]), float(o[1]))
    return None
