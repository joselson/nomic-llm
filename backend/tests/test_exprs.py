import math

import pytest
from pydantic import TypeAdapter

from game.dsl import Expr
from game.engine.exprs import Ctx, evaluate

from .helpers import make_world, place

E = TypeAdapter(Expr)


def ev(w, raw, **ctx):
    return evaluate(w, E.validate_python(raw), Ctx(**ctx))


@pytest.fixture
def w():
    return make_world(players=3)


@pytest.mark.parametrize("raw,expected", [
    ({"op": "add", "args": [1, 2, 3]}, 6),
    ({"op": "sub", "args": [10, 3, 2]}, 5),
    ({"op": "mul", "args": [2, 3, 4]}, 24),
    ({"op": "div", "args": [7, 2]}, 3.5),
    ({"op": "div", "args": [1, 0]}, 0),
    ({"op": "mod", "args": [7, 3]}, 1),
    ({"op": "mod", "args": [7, 0]}, 0),
    ({"op": "pow", "args": [2, 10]}, 1024),
    ({"op": "neg", "args": [4]}, -4),
    ({"op": "abs", "args": [-4]}, 4),
    ({"op": "min", "args": [3, 1, 2]}, 1),
    ({"op": "max", "args": [3, 1, 2]}, 3),
    ({"op": "floor", "args": [2.7]}, 2),
    ({"op": "ceil", "args": [2.1]}, 3),
    ({"op": "round", "args": [2.5]}, 2),
    ({"op": "clamp", "args": [15, 0, 10]}, 10),
    ({"op": "eq", "args": [1, 1.0]}, True),
    ({"op": "ne", "args": ["a", "b"]}, True),
    ({"op": "lt", "args": [1, 2]}, True),
    ({"op": "ge", "args": [2, 2]}, True),
    ({"op": "and", "args": [True, 1, "x"]}, True),
    ({"op": "or", "args": [False, 0]}, False),
    ({"op": "not", "args": [0]}, True),
    ({"op": "if", "args": [True, "yes", "no"]}, "yes"),
    ({"op": "concat", "args": ["a", 1, True]}, "a1True"),
    ({"op": "len", "args": ["hello"]}, 5),
    ({"op": "contains", "args": ["Hello World", "world"]}, True),
    ({"op": "lower", "args": ["ABC"]}, "abc"),
    ({"op": "add", "args": ["x", 2]}, 2),          # bad types coerce, never raise
    ({"op": "gt", "args": [{"ref": "actor.nothing"}, -1]}, True),
])
def test_ops(w, raw, expected):
    assert ev(w, raw) == expected


def test_random_ops_are_bounded(w):
    for _ in range(50):
        assert 0 <= ev(w, {"op": "random"}) < 1
        assert 2 <= ev(w, {"op": "random", "args": [2, 3]}) <= 3
        assert ev(w, {"op": "random_int", "args": [1, 3]}) in (1, 2, 3)
        assert ev(w, {"op": "pick", "args": ["a", "b"]}) in ("a", "b")
    assert ev(w, {"op": "chance", "args": [1]}) is True
    assert ev(w, {"op": "chance", "args": [0]}) is False


def test_refs_and_builtins(w):
    p = w.players["p1"]
    p.points = 7
    p.x, p.y = 3, 4
    assert ev(w, {"ref": "actor.points"}, actor=p) == 7
    assert ev(w, {"ref": "actor.name"}, actor=p) == "P1"
    assert ev(w, {"ref": "actor"}, actor=p) is p
    assert ev(w, {"ref": "target.points"}) == 0          # missing object → 0
    assert ev(w, {"ref": "world.day"}) == 1
    assert ev(w, {"ref": "world.player_count"}) == 3
    assert ev(w, {"ref": "event.amount"}, event={"amount": 4}) == 4
    assert ev(w, {"ref": "event.from.name"}, event={"from": p}) == "P1"
    assert ev(w, {"ref": "vars.x"}, vars={"x": 9}) == 9


def test_spatial_and_game_ops(w):
    a, b = w.players["p1"], w.players["p2"]
    place(w, "p1", 10, 10)
    place(w, "p2", 13, 14)
    a.facing = 0
    assert ev(w, {"op": "distance", "args": [{"ref": "actor"}, {"ref": "target"}]}, actor=a, target=b) == 5
    assert math.isclose(ev(w, {"op": "angle_to", "args": [{"ref": "actor"}, {"ref": "target"}]}, actor=a, target=b),
                        math.degrees(math.atan2(4, 3)))
    assert ev(w, {"op": "facing", "args": [{"ref": "actor"}, {"ref": "target"}]}, actor=a, target=b) is True
    a.facing = 180
    assert ev(w, {"op": "facing", "args": [{"ref": "actor"}, {"ref": "target"}]}, actor=a, target=b) is False
    a.points, b.points = 5, 9
    assert ev(w, {"op": "rank", "args": [{"ref": "actor"}]}, actor=a) == 2
    assert ev(w, {"op": "rank", "args": [{"ref": "actor"}]}, actor=b) == 1
    assert ev(w, {"op": "player_count"}) == 3
    assert ev(w, {"op": "day"}) == 1


def test_aggregates(w):
    for i, p in enumerate(w.players.values()):
        p.points = i * 2
    count = {"agg": "count", "of": {"kind": "players"}}
    assert ev(w, count) == 3
    assert ev(w, {"agg": "sum", "of": {"kind": "players"}, "value": {"ref": "it.points"}}) == 6
    assert ev(w, {"agg": "max", "of": {"kind": "players"}, "value": {"ref": "it.points"}}) == 4
    assert ev(w, {"agg": "avg", "of": {"kind": "players"}, "value": {"ref": "it.points"}}) == 2
    assert ev(w, {"agg": "any", "of": {"kind": "players"}, "value": {"op": "gt", "args": [{"ref": "it.points"}, 3]}})
    assert not ev(w, {"agg": "all", "of": {"kind": "players"}, "value": {"op": "gt", "args": [{"ref": "it.points"}, 3]}})
    assert ev(w, {"agg": "sum", "of": {"kind": "entities"}, "value": 1}) == 0
