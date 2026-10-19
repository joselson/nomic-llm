"""Dispatch, triggers, selectors, safety nets."""
from game.engine.events import Event

from .helpers import entities_of, hold, make_world, mech, park_players, place, run

TRIG = {
    "id": "trig", "name": "Trig", "summary": "s",
    "world_props": [{"name": "hits", "default": 0}, {"name": "enters", "default": 0},
                    {"name": "nears", "default": 0}, {"name": "idles", "default": 0},
                    {"name": "chats", "default": 0}, {"name": "starts", "default": 0},
                    {"name": "ends", "default": 0}],
    "entities": [{"type": "post", "name": "Post", "solid": True, "tags": ["wood"],
                  "spawn": {"count": 1, "at": {"x": 30, "y": 30}}}],
    "zones": [{"zone": "pad", "name": "Pad", "at": {"x": 10, "y": 10}, "radius": 1.5}],
    "rules": [
        {"id": "hit", "trigger": {"on": "collide", "tag": "wood"},
         "do": [{"fx": "change", "target": "world", "prop": "hits", "delta": 1}], "limits": {"cooldown_s": 0}},
        {"id": "enter", "trigger": {"on": "enter_zone", "zone": "pad"},
         "do": [{"fx": "change", "target": "world", "prop": "enters", "delta": 1}], "limits": {"cooldown_s": 0}},
        {"id": "near", "trigger": {"on": "near", "entity": "post", "radius": 3},
         "do": [{"fx": "change", "target": "world", "prop": "nears", "delta": 1}], "limits": {"cooldown_s": 0}},
        {"id": "idle", "trigger": {"on": "idle", "idle_s": 5},
         "do": [{"fx": "change", "target": "world", "prop": "idles", "delta": 1}], "limits": {"cooldown_s": 0}},
        {"id": "chat", "trigger": {"on": "chat", "chat_contains": "Hello"},
         "do": [{"fx": "change", "target": "world", "prop": "chats", "delta": 1}], "limits": {"cooldown_s": 0}},
        {"id": "start", "trigger": {"on": "game_start"},
         "do": [{"fx": "change", "target": "world", "prop": "starts", "delta": 1}]},
        {"id": "end", "trigger": {"on": "day_end"},
         "do": [{"fx": "change", "target": "world", "prop": "ends", "delta": 1}]},
    ],
}


def test_contact_triggers_are_edge_triggered():
    w = make_world(TRIG, players=2)
    park_players(w)
    place(w, "p1", 30 - 0.5 - 0.4 - 0.02, 30)          # touching the post
    run(w, 1)
    assert w.world_props["hits"] == 1                   # once per contact, not per tick
    assert w.world_props["nears"] == 1
    place(w, "p1", 20, 20)
    run(w, 0.1)
    place(w, "p1", 30 - 0.5 - 0.4 - 0.02, 30)
    run(w, 0.1)
    assert w.world_props["hits"] == 2 and w.world_props["nears"] == 2


def test_zone_enter_and_idle_and_chat():
    w = make_world(TRIG, players=2)
    park_players(w)
    place(w, "p1", 10, 10)
    run(w, 0.1)
    assert w.world_props["enters"] == 1
    run(w, 5)
    assert w.world_props["idles"] == 2                  # both players idle once
    hold(w, "p1", 1, 0, 0.2)
    run(w, 5.1)
    assert w.world_props["idles"] == 3
    w.queue_action("chat", "p1", "well hello there")
    w.queue_action("chat", "p1", "bye")
    run(w, 0.05)
    assert w.world_props["chats"] == 1


def test_game_start_only_on_day_one_and_day_end():
    w = make_world(TRIG, day=1)
    run(w, 0.05)
    assert w.world_props["starts"] == 1
    w.end_day()
    assert w.world_props["ends"] == 1
    w2 = make_world(TRIG, day=3)
    run(w2, 0.05)
    assert w2.world_props["starts"] == 0


def test_selectors():
    from game.dsl import Selector
    from game.engine.exprs import Ctx
    from game.engine.targets import select
    w = make_world(TRIG, players=4)
    park_players(w)
    for i, p in enumerate(w.players.values()):
        p.points = i
    c = Ctx(actor=w.players["p1"])
    sel = Selector.model_validate({"kind": "players", "sort_by": {"ref": "it.points"}, "descending": True, "limit": 2})
    assert [p.id for p in select(w, sel, c)] == ["p4", "p3"]
    sel = Selector.model_validate({"kind": "players", "exclude_actor": True})
    assert "p1" not in [p.id for p in select(w, sel, c)]
    sel = Selector.model_validate({"kind": "players", "near": "actor", "radius": 3})
    assert [p.id for p in select(w, sel, c)] == ["p1"]
    sel = Selector.model_validate({"kind": "entities", "tags": ["wood"]})
    assert len(select(w, sel, c)) == 1
    sel = Selector.model_validate({"kind": "zones", "types": ["pad"]})
    assert len(select(w, sel, c)) == 1
    sel = Selector.model_validate({"kind": "players", "where": {"op": "ge", "args": [{"ref": "it.points"}, 2]}})
    assert {p.id for p in select(w, sel, c)} == {"p3", "p4"}


BROKEN = {
    "id": "broken", "name": "Broken", "summary": "s",
    "rules": [{"id": "boom", "trigger": {"on": "every", "every_s": 1},
               "do": [{"fx": "message", "text": "tick"}]}],
}


def test_rule_auto_disabled_after_three_errors(monkeypatch):
    w = make_world(BROKEN)
    from game.engine import effects

    def explode(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setitem(effects.EXECUTORS, type(w.mechanics["broken"].rules[0].do[0]), explode)
    run(w, 5.1)
    assert "broken/boom" in w.disabled_rules
    assert w.errors["broken/boom"] == 3
    assert w.incidents


LOOP = {
    "id": "loop", "name": "Loop", "summary": "s",
    "world_props": [{"name": "n", "default": 0}],
    "rules": [{"id": "echo", "trigger": {"on": "custom", "event": "ping"},
               "do": [{"fx": "change", "target": "world", "prop": "n", "delta": 1},
                      {"fx": "emit", "event": "ping"}],
               "limits": {"cooldown_s": 0}}],
}


def test_event_chain_depth_is_capped():
    w = make_world(LOOP)
    w.push(Event("custom", None, None, {"event": "ping"}))
    w.queue.drain()
    assert w.world_props["n"] == 9          # depth 0..8
    assert w.queue.dropped >= 1


EXCL = {
    "id": "excl", "name": "Excl", "summary": "s",
    "abilities": [{"ability": "wave", "name": "Wave", "do": [{"fx": "give", "item": "token"}],
                   "limits": {"cooldown_s": 0}}],
    "items": [{"item": "token", "name": "Token"}],
    "creator_perk": {"exclusive_s": 60},
}


def test_creator_exclusivity_window():
    w = make_world(EXCL, players=2)
    w.queue_action("use_ability", "p2", "wave")
    w.queue_action("use_ability", "p1", "wave")
    run(w, 0.1)
    assert "token" not in w.players["p2"].items
    assert w.players["p1"].items["token"] == 1
    run(w, 60)
    w.queue_action("use_ability", "p2", "wave")
    run(w, 0.1)
    assert w.players["p2"].items["token"] == 1


def test_patch_hot_reload_does_not_respawn():
    from game import dsl_examples as ex
    w = make_world(ex.APPLE_TREES)
    ids = {e.id for e in entities_of(w, "apple_tree")}
    w.load_mechanic(mech(ex.FERTILIZER, day=1))
    assert {e.id for e in entities_of(w, "apple_tree")} == ids
    assert w.mechanics["apple_trees"].rules[0].limits.cooldown_s == 10


def test_disable_patch_turns_rule_off():
    w = make_world(LOOP)
    off = {"id": "off", "name": "Off", "summary": "s",
           "patches": [{"mechanic": "loop", "op": "disable", "path": "/rules/echo"}]}
    w.load_mechanic(mech(off))
    w.push(Event("custom", None, None, {"event": "ping"}))
    w.queue.drain()
    assert w.world_props["n"] == 0


WANDER = {
    "id": "zoo", "name": "Zoo", "summary": "s",
    "entities": [
        {"type": "sheep", "name": "Sheep", "behavior": {"kind": "wander", "speed": 2}, "spawn": {"count": 3}},
        {"type": "wolf", "name": "Wolf", "behavior": {"kind": "chase", "speed": 3, "range": 50},
         "spawn": {"count": 1, "at": {"x": 5, "y": 5}}},
        {"type": "moon", "name": "Moon", "behavior": {"kind": "orbit", "range": 3, "speed": 3},
         "spawn": {"count": 1, "at": "center"}},
        {"type": "guard", "name": "Guard", "behavior": {"kind": "guard", "range": 4, "speed": 3},
         "spawn": {"count": 1, "at": {"x": 40, "y": 40}}},
        {"type": "ball", "name": "Ball", "behavior": {"kind": "bounce", "speed": 6}, "spawn": {"count": 1}},
        {"type": "drone", "name": "Drone", "behavior": {"kind": "patrol", "speed": 4,
                                                        "path": [{"x": 5, "y": 40}, {"x": 10, "y": 40}]},
         "spawn": {"count": 1, "at": {"x": 5, "y": 40}}},
        {"type": "cart", "name": "Cart", "behavior": {"kind": "move_to", "speed": 5, "stop_at": {"x": 40, "y": 5}},
         "spawn": {"count": 1, "at": {"x": 30, "y": 5}}},
        {"type": "mouse", "name": "Mouse", "behavior": {"kind": "flee", "speed": 3, "range": 6},
         "spawn": {"count": 1, "at": {"x": 20, "y": 30}}},
    ],
}


def test_behaviors_move_entities():
    w = make_world(WANDER, players=1)
    place(w, "p1", 20, 20)
    sheep0 = {e.id: (e.x, e.y) for e in entities_of(w, "sheep")}
    wolf = entities_of(w, "wolf")[0]
    cart = entities_of(w, "cart")[0]
    mouse = entities_of(w, "mouse")[0]
    place(w, "p1", 20, 27)
    run(w, 10)
    assert any((e.x, e.y) != sheep0[e.id] for e in entities_of(w, "sheep"))
    assert ((wolf.x - 20) ** 2 + (wolf.y - 27) ** 2) ** 0.5 < 2
    assert abs(cart.x - 40) < 0.3
    assert mouse.y > 30
    moon = entities_of(w, "moon")[0]
    assert abs(((moon.x - moon.spawn_x) ** 2 + (moon.y - moon.spawn_y) ** 2) ** 0.5 - 3) < 0.2
    drone = entities_of(w, "drone")[0]
    assert 4.5 <= drone.x <= 10.5
    for e in entities_of(w, "ball"):
        assert 0 <= e.x <= w.map_size and 0 <= e.y <= w.map_size


def test_entity_cap():
    w = make_world(players=1, max_entities=20)
    many = {"id": "many", "name": "Many", "summary": "s",
            "entities": [{"type": "dot", "name": "Dot", "spawn": {"count": 50}, "max_alive": 100}]}
    w.load_mechanic(mech(many))
    assert len(w.entities) == 20


def test_snapshot_hides_invisible_players_from_others():
    inv = {"id": "inv", "name": "Inv", "summary": "s",
           "statuses": [{"status": "cloak", "name": "Cloak", "duration_s": 10, "flags": ["invisible"]}]}
    w = make_world(inv, players=2)
    w.apply_status(w.players["p1"], "cloak")
    common = w.snapshot_common()
    assert {p["id"] for p in w.snapshot_for("p2", common)["players"]} == {"p2"}
    assert {p["id"] for p in w.snapshot_for("p1", common)["players"]} == {"p1", "p2"}
