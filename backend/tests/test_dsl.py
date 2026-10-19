import pytest
from pydantic import ValidationError

from game import dsl_examples as ex
from game.dsl import Mechanic, Patch, apply_patch, validate_mechanic
from game.engine import support

from .helpers import mech


def test_examples_validate_in_order():
    world = []
    for raw in [ex.APPLE_TREES, ex.HOT_POTATO, ex.KING_OF_THE_HILL, ex.APPLE_DASH, ex.ORACLE, ex.FERTILIZER]:
        m = Mechanic.model_validate(raw)
        errors, _ = validate_mechanic(m, world)
        assert not errors, (m.id, errors)
        for p in m.patches:
            i = next(i for i, x in enumerate(world) if x.id == p.mechanic)
            world[i] = apply_patch(world[i], p)
        world.append(m)


def test_cheater_and_unknown_refs_rejected():
    with pytest.raises(ValidationError):
        Mechanic.model_validate(ex.CHEATER)
    errs, _ = validate_mechanic(Mechanic.model_validate(ex.UNKNOWN_REF), [])
    assert any("rock" in e for e in errs) and any("gold" in e for e in errs)


def test_unsupported_features_are_rejected(monkeypatch):
    monkeypatch.setattr(support, "UNSUPPORTED_FX", {"paint"})
    monkeypatch.setattr(support, "UNSUPPORTED_OPS", {"facing"})
    raw = {"id": "x", "name": "X", "summary": "s", "tiles": [{"tile": "t", "color": "#000000"}],
           "rules": [{"id": "r", "trigger": {"on": "day_start"},
                      "if": [{"op": "facing", "args": [{"ref": "actor"}, {"ref": "target"}]}],
                      "do": [{"fx": "paint", "tile": "t"}]}]}
    errs, _ = validate_mechanic(mech(raw), [])
    assert any("paint" in e for e in errs) and any("facing" in e for e in errs)


def test_support_sets_empty_for_mvp():
    assert not (support.UNSUPPORTED_FX | support.UNSUPPORTED_TRIGGERS | support.UNSUPPORTED_OPS
                | support.UNSUPPORTED_BEHAVIORS)


def test_props_cannot_shadow_builtins_or_items():
    raw = {"id": "x", "name": "X", "summary": "s", "player_props": [{"name": "points"}],
           "items": [{"item": "gem", "name": "Gem"}], "world_props": [{"name": "gem"}]}
    errs, _ = validate_mechanic(mech(raw), [])
    assert any("built-in" in e for e in errs) and any("same id as an item" in e for e in errs)


def test_redefining_existing_ids_is_an_error():
    a = mech(ex.APPLE_TREES)
    clone = dict(ex.APPLE_TREES, id="apple_trees_2")
    errs, _ = validate_mechanic(mech(clone), [a])
    assert any("already defined" in e for e in errs)


def test_patches():
    trees = mech(ex.APPLE_TREES)
    t2 = apply_patch(trees, Patch(mechanic="apple_trees", op="scale", path="/rules/shake_tree/limits/cooldown_s", value=2))
    assert t2.rules[0].limits.cooldown_s == 10
    t3 = apply_patch(trees, Patch(mechanic="apple_trees", op="add", path="/entities/apple_tree/spawn/count", value=4))
    assert t3.entities[0].spawn.count == 10
    t4 = apply_patch(trees, Patch(mechanic="apple_trees", op="remove", path="/rules/regrow"))
    assert [r.id for r in t4.rules] == ["shake_tree", "eat_apples"]
    t5 = apply_patch(trees, Patch(mechanic="apple_trees", op="disable", path=""))
    assert t5.disabled
    with pytest.raises(ValueError):
        apply_patch(trees, Patch(mechanic="apple_trees", op="set", path="/creator", value="me"))
    with pytest.raises(KeyError):
        apply_patch(trees, Patch(mechanic="apple_trees", op="set", path="/rules/nope/limits", value=1))
    errs, _ = validate_mechanic(mech({"id": "bad", "name": "B", "summary": "s",
                                      "patches": [{"mechanic": "ghost", "op": "disable"}]}), [trees])
    assert errs
