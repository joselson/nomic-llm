"""One test (at least) per effect executor."""
from game.engine.effects import render_template
from game.engine.exprs import Ctx

from .helpers import entities_of, hold, make_world, park_players, place, run

BASE = {
    "id": "kit", "name": "Kit", "summary": "test kit",
    "player_props": [{"name": "mana", "default": 5, "min": 0, "max": 10},
                     {"name": "buddy", "type": "player", "default": None}],
    "world_props": [{"name": "pot", "default": 0}],
    "items": [{"item": "gem", "name": "Gem", "stack_max": 5}],
    "entities": [{"type": "rock", "name": "Rock", "hp": 3, "solid": True,
                  "properties": [{"name": "ore", "default": 2, "visible_to": "everyone"}]},
                 {"type": "coin", "name": "Coin", "collectible": True},
                 {"type": "bolt", "name": "Bolt", "behavior": {"kind": "projectile", "speed": 10}},
                 {"type": "pet", "name": "Pet", "behavior": {"kind": "follow", "target": "actor", "speed": 3}}],
    "tiles": [{"tile": "mud", "color": "#7a5230", "speed_mult": 0.5},
              {"tile": "wall", "color": "#333333", "walkable": False}],
    "zones": [{"zone": "pool", "name": "Pool", "radius": 2, "placed_at_start": False}],
    "statuses": [{"status": "slow", "name": "Slow", "duration_s": 5,
                  "modifiers": [{"stat": "speed", "op": "mul", "value": 0.5}]},
                 {"status": "shield", "name": "Shield", "duration_s": 5, "flags": ["immune"]},
                 {"status": "burn", "name": "Burn", "duration_s": 3, "tick_s": 1,
                  "on_tick": [{"fx": "damage", "target": "actor", "amount": 1}]}],
    "abilities": [{"ability": "zap", "name": "Zap", "available_to": "granted", "do": [{"fx": "stop"}],
                   "limits": {"cooldown_s": 1}}],
    "rules": [{"id": "noop", "trigger": {"on": "custom", "event": "ping"},
               "do": [{"fx": "set", "target": "world", "prop": "pot", "value": {"ref": "event.n"}}],
               "limits": {"cooldown_s": 0}}],
}


def run_fx(w, effects, **ctx):
    from pydantic import TypeAdapter
    from game.dsl import Effect
    ad = TypeAdapter(list[Effect])
    m = w.mechanics["kit"]
    c = Ctx(mechanic=m, **ctx)
    w.run(ad.validate_python(effects), c)
    w.queue.drain()


def world(players=2):
    w = make_world(BASE, players=players)
    park_players(w)
    return w


def test_set_and_change_clamp_and_emit():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "set", "prop": "mana", "value": 50}], actor=p)
    assert p.props["mana"] == 10
    run_fx(w, [{"fx": "change", "prop": "mana", "delta": -20}], actor=p)
    assert p.props["mana"] == 0
    run_fx(w, [{"fx": "set", "target": "world", "prop": "pot", "value": 3}])
    assert w.world_props["pot"] == 3
    run_fx(w, [{"fx": "set", "prop": "buddy", "value": {"ref": "target"}}], actor=p, target=w.players["p2"])
    assert p.props["buddy"] == "p2"


def test_add_points_via_ledger_and_floor():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "add_points", "amount": 3}], actor=p)
    assert p.points == 3
    run_fx(w, [{"fx": "add_points", "amount": 5}], actor=p)
    assert p.points == 5                                  # capped at 5/day for this mechanic
    run_fx(w, [{"fx": "add_points", "to": "everyone", "amount": -9}], actor=p)
    assert p.points == 0 and w.players["p2"].points == 0


def test_give_take_stack_max_and_event():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "give", "item": "gem", "amount": 9}], actor=p)
    assert p.items["gem"] == 5
    run_fx(w, [{"fx": "give", "item": "gem", "amount": -7}], actor=p)
    assert "gem" not in p.items


def test_transfer_items_props_points():
    w = world()
    a, b = w.players["p1"], w.players["p2"]
    a.items["gem"] = 3
    run_fx(w, [{"fx": "transfer", "from": "actor", "to": "target", "what": "gem", "amount": 5}], actor=a, target=b)
    assert b.items["gem"] == 3 and "gem" not in a.items
    run_fx(w, [{"fx": "transfer", "from": "actor", "to": "target", "what": "mana", "amount": 2}], actor=a, target=b)
    assert a.props["mana"] == 3 and b.props["mana"] == 7
    a.points = 4
    run_fx(w, [{"fx": "transfer", "from": "actor", "to": "target", "what": "points", "amount": 2}], actor=a, target=b)
    assert a.points == 2 and b.points == 2


def test_spawn_destroy_and_caps():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "spawn", "type": "rock", "at": "center", "count": 3, "scatter": 2,
                "props": {"ore": 9}}], actor=p)
    rocks = entities_of(w, "rock")
    assert len(rocks) == 3 and all(r.props["ore"] == 9 for r in rocks)
    run_fx(w, [{"fx": "destroy", "target": {"types": ["rock"]}}], actor=p)
    assert not entities_of(w, "rock")
    run_fx(w, [{"fx": "spawn", "type": "rock", "count": 500}], actor=p)
    assert len(entities_of(w, "rock")) == 50              # per-effect cap / max_alive


def test_move_modes():
    w = world()
    a, b = w.players["p1"], w.players["p2"]
    place(w, "p1", 10, 10)
    place(w, "p2", 12, 10)
    run_fx(w, [{"fx": "move", "target": "target", "mode": "push", "distance": 4}], actor=a, target=b)
    run(w, 1)
    assert b.x > 15
    run_fx(w, [{"fx": "move", "target": "target", "mode": "pull", "distance": 3}], actor=a, target=b)
    run(w, 1)
    assert b.x < 14
    run_fx(w, [{"fx": "move", "mode": "teleport", "to": {"x": 30, "y": 31}}], actor=a)
    assert (a.x, a.y) == (30, 31)
    run_fx(w, [{"fx": "move", "mode": "swap", "target": "target"}], actor=a, target=b)
    assert a.x < 15 and b.x == 30
    a.facing = 90
    y0 = a.y
    run_fx(w, [{"fx": "move", "mode": "dash", "distance": 3}], actor=a)
    run(w, 1)
    assert a.y > y0 + 2.5
    run_fx(w, [{"fx": "move", "mode": "walk_to", "to": "center", "speed": 20}], actor=a)
    run(w, 3)
    assert abs(a.x - 24) < 0.6 and abs(a.y - 24) < 0.6


def test_damage_defeat_respawn_and_entity_death():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "damage", "target": "actor", "amount": 4}], actor=p)
    assert p.hp == 6
    run_fx(w, [{"fx": "damage", "target": "actor", "amount": -10}], actor=p)
    assert p.hp == 10
    run_fx(w, [{"fx": "damage", "target": "actor", "amount": 99}], actor=p)
    assert not p.alive
    run(w, 3.1)
    assert p.alive and p.hp == 10
    run_fx(w, [{"fx": "spawn", "type": "rock", "at": "center"}], actor=p)
    rock = entities_of(w, "rock")[0]
    run_fx(w, [{"fx": "damage", "target": {"types": ["rock"]}, "amount": 5}], actor=p)
    assert rock.dead


def test_statuses_stack_modifiers_ticks_and_immunity():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "apply_status", "status": "slow"}], actor=p)
    run(w, 0.05)
    assert p.stats["speed"] == 2.0
    run_fx(w, [{"fx": "remove_status", "status": "slow"}], actor=p)
    assert not p.statuses and p.stats["speed"] == 4.0
    run_fx(w, [{"fx": "apply_status", "status": "burn"}], actor=p)
    run(w, 3.2)
    assert p.hp == 7 and not p.statuses
    run_fx(w, [{"fx": "apply_status", "status": "shield"}], actor=p)
    run_fx(w, [{"fx": "apply_status", "status": "slow"}], actor=p)
    assert [s.status for s in p.statuses] == ["shield"]


def test_grant_and_revoke_ability_uses():
    w = world()
    p = w.players["p1"]
    assert not w.player_has_ability(p, "zap")
    run_fx(w, [{"fx": "grant_ability", "ability": "zap", "uses": 2}], actor=p)
    assert w.player_has_ability(p, "zap")
    w.queue_action("use_ability", "p1", "zap")
    run(w, 1.1)
    w.queue_action("use_ability", "p1", "zap")
    run(w, 0.1)
    assert not w.player_has_ability(p, "zap")
    run_fx(w, [{"fx": "grant_ability", "ability": "zap"}, {"fx": "revoke_ability", "ability": "zap"}], actor=p)
    assert not w.player_has_ability(p, "zap")


def test_zones_and_paint():
    w = world()
    p = w.players["p1"]
    place(w, "p1", 20, 20)
    run_fx(w, [{"fx": "create_zone", "zone": "pool", "at": "actor", "duration_s": 2}], actor=p)
    assert w.in_zone(20, 20, "pool")
    run(w, 2.1)
    assert not w.in_zone(20, 20, "pool")
    run_fx(w, [{"fx": "create_zone", "zone": "pool", "at": "actor"}, {"fx": "remove_zone", "zone": "pool"}], actor=p)
    assert not w.zones
    run_fx(w, [{"fx": "paint", "tile": "mud", "at": "actor", "radius": 1, "duration_s": 1}], actor=p)
    assert w.tiles.at(20, 20) == "mud"
    run(w, 1.1)
    assert w.tiles.at(20, 20) is None


def test_set_visual_reverts():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "set_visual", "visual": {"color": "#ff0000", "emoji": "🔥"}, "duration_s": 1}], actor=p)
    assert p.visual == {"color": "#ff0000", "emoji": "🔥"}
    run(w, 1.1)
    assert p.visual == {}


def test_emit_custom_event():
    w = world()
    run_fx(w, [{"fx": "emit", "event": "ping", "data": {"n": 42}}])
    assert w.world_props["pot"] == 42


def test_after_and_repeat():
    w = world()
    p = w.players["p1"]
    run_fx(w, [{"fx": "after", "delay_s": 1, "do": [{"fx": "give", "item": "gem"}]},
               {"fx": "repeat", "every_s": 1, "times": 3, "do": [{"fx": "change", "prop": "mana", "delta": 1}]}],
           actor=p)
    run(w, 0.5)
    assert "gem" not in p.items
    run(w, 3)
    assert p.items["gem"] == 1 and p.props["mana"] == 8


def test_control_flow():
    w = world()
    p = w.players["p1"]
    run_fx(w, [
        {"fx": "let", "name": "n", "value": 3},
        {"fx": "if", "cond": {"op": "gt", "args": [{"ref": "vars.n"}, 2]},
         "then": [{"fx": "give", "item": "gem", "amount": {"ref": "vars.n"}}], "else": []},
        {"fx": "for_each", "in": {"kind": "players"}, "do": [{"fx": "change", "target": "it", "prop": "mana", "delta": 1}]},
        {"fx": "chance", "p": 1, "do": [{"fx": "set", "target": "world", "prop": "pot", "value": 1}], "else": []},
        {"fx": "pick_one", "options": [{"weight": 1, "do": []}, {"weight": 0.0001, "do": []}]},
        {"fx": "stop"},
        {"fx": "give", "item": "gem", "amount": 1},
    ], actor=p)
    assert p.items["gem"] == 3
    assert all(q.props["mana"] == 6 for q in w.players.values())
    assert w.world_props["pot"] == 1


def test_message_fx_and_templates():
    w = world()
    p = w.players["p1"]
    w.take_outbox()
    run_fx(w, [{"fx": "message", "text": "hi {actor.name}, mana {actor.mana} {nope}"},
               {"fx": "message", "to": "everyone", "style": "announce", "text": "boom"},
               {"fx": "fx", "kind": "burst", "color": "#ff0000"}], actor=p)
    out = w.take_outbox()
    assert out[0] == (["p1"], {"t": "message", "text": "hi P1, mana 5 {nope}", "style": "toast", "from": None})
    assert out[1][0] is None and out[1][1]["style"] == "announce"
    assert out[2][1]["t"] == "fx" and out[2][1]["kind"] == "burst"
    assert render_template(w, "{world.day}", Ctx()) == "1"


def test_prompt_answer_and_timeout_default():
    w = world()
    p = w.players["p1"]
    w.take_outbox()
    run_fx(w, [{"fx": "prompt", "question": "Gem?", "timeout_s": 3, "default": 1, "options": [
        {"label": "Yes", "do": [{"fx": "give", "item": "gem", "amount": 2}]},
        {"label": "No", "do": [{"fx": "give", "item": "gem", "amount": 1}]}]}], actor=p)
    q = [m for _, m in w.take_outbox() if m["t"] == "prompt"][0]
    w.queue_action("answer_prompt", "p1", q["prompt_id"], 0)
    run(w, 0.05)
    assert p.items["gem"] == 2
    run_fx(w, [{"fx": "prompt", "question": "Again?", "timeout_s": 3, "default": 1, "options": [
        {"label": "Yes", "do": [{"fx": "give", "item": "gem", "amount": 2}]},
        {"label": "No", "do": [{"fx": "give", "item": "gem", "amount": 1}]}]}], actor=p)
    run(w, 3.1)
    assert p.items["gem"] == 3


def test_vote_runs_winner_for_each_supporter():
    w = world(players=3)
    w.take_outbox()
    run_fx(w, [{"fx": "vote", "question": "Gems?", "duration_s": 10, "options": [
        {"label": "Gems", "do": [{"fx": "give", "item": "gem"}]},
        {"label": "Mana", "do": [{"fx": "change", "prop": "mana", "delta": 1}]}]}])
    vid = [m for _, m in w.take_outbox() if m["t"] == "prompt"][0]["prompt_id"]
    w.queue_action("answer_prompt", "p1", vid, 0)
    w.queue_action("answer_prompt", "p2", vid, 0)
    w.queue_action("answer_prompt", "p3", vid, 1)
    run(w, 0.1)
    assert w.players["p1"].items["gem"] == 1 and w.players["p2"].items["gem"] == 1
    assert "gem" not in w.players["p3"].items and w.players["p3"].props["mana"] == 5


def test_collect_and_projectile_and_follow():
    w = world()
    p = w.players["p1"]
    place(w, "p1", 20, 20)
    run_fx(w, [{"fx": "spawn", "type": "coin", "at": {"x": 21, "y": 20}}], actor=p)
    hold(w, "p1", 1, 0, 0.3)
    assert not entities_of(w, "coin")
    q = w.players["p2"]
    place(w, "p2", 30, 20)
    p.facing = 0
    run_fx(w, [{"fx": "spawn", "type": "bolt", "at": "actor"}], actor=p)
    run(w, 1.5)
    assert not entities_of(w, "bolt")                     # hit p2 or left the map
    run_fx(w, [{"fx": "spawn", "type": "pet", "at": {"x": 5, "y": 5}, "owner": "actor"}], actor=p)
    pet = entities_of(w, "pet")[0]
    run(w, 8)
    assert ((pet.x - p.x) ** 2 + (pet.y - p.y) ** 2) ** 0.5 < 3
    assert q.alive


def test_terrain_speed_and_walls():
    w = world()
    p = w.players["p1"]
    place(w, "p1", 10.5, 10.5)
    run_fx(w, [{"fx": "paint", "tile": "mud", "at": "actor", "radius": 3}], actor=p)
    hold(w, "p1", 1, 0, 1)
    assert abs(p.x - 12.5) < 0.05          # half speed in mud
    run_fx(w, [{"fx": "paint", "tile": "wall", "at": {"x": 14.5, "y": 10.5}, "radius": 0.5}], actor=p)
    hold(w, "p1", 1, 0, 2)
    assert p.x < 14
