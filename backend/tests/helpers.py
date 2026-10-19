"""Test helpers: build worlds from raw mechanic dicts and drive them."""
from __future__ import annotations

import copy

from game.dsl import Mechanic, validate_mechanic
from game.engine.world import World

DT = 0.05


def mech(raw: dict, creator: str = "p1", day: int = 1, **overrides) -> Mechanic:
    data = copy.deepcopy(raw)
    data["creator"] = creator
    data["day"] = day
    data.update(overrides)
    return Mechanic.model_validate(data)


def make_world(*raws: dict, players: int = 2, day: int = 1, seed: int = 7, validate: bool = True,
               **kw) -> World:
    w = World(seed=seed, **kw)
    for i in range(players):
        w.add_player(f"p{i + 1}", f"P{i + 1}")
    w.day = day
    w.phase = "night"          # loading happens before the day starts, like a deploy
    for raw in raws:
        m = mech(raw, day=day) if isinstance(raw, dict) else raw
        if validate:
            errors, _ = validate_mechanic(m, list(w.mechanics.values()))
            assert not errors, errors
        w.load_mechanic(m)
    w.start_day(day, 420)
    return w


def run(w: World, seconds: float) -> None:
    for _ in range(int(round(seconds / DT))):
        w.tick(DT)


def place(w: World, pid: str, x: float, y: float) -> None:
    p = w.players[pid]
    p.x, p.y = x, y


def entities_of(w: World, etype: str):
    return [e for e in w.entities.values() if e.type == etype and not e.dead]


def messages(w: World, t: str | None = None):
    out = w.take_outbox()
    return [m for _, m in out if t is None or m["t"] == t]


def park_players(w: World) -> None:
    """Move every player into its own corner so nothing touches by accident."""
    corners = [(2, 2), (w.map_size - 2, 2), (2, w.map_size - 2), (w.map_size - 2, w.map_size - 2)]
    for p, (x, y) in zip(w.players.values(), corners):
        p.x, p.y = x, y


def hold(w: World, pid: str, dx: float, dy: float, seconds: float) -> None:
    """Simulate a client holding a direction: one input command per tick."""
    p = w.players[pid]
    for _ in range(int(round(seconds / DT))):
        w.set_input(pid, dx, dy, seq=p.input_seq + 1 + len(p.input_queue))
        w.tick(DT)
