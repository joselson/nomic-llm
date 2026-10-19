"""Example mechanics as an agent would emit them, plus a self-test.
Run from backend/: python -m game.dsl_examples
"""
import json

from pydantic import ValidationError

from game.dsl import Mechanic, apply_patch, validate_mechanic

APPLE_TREES = {  # day 1 — resources + entity state + daily reset
    "id": "apple_trees", "name": "Apple Trees", "creator": "ze", "day": 1,
    "summary": "Trees sprout across the map. Shake them for apples; eat 3 apples for 1 point.",
    "items": [{"item": "apple", "name": "Apple", "visual": {"emoji": "🍎", "color": "#d33c3c"},
               "usable": True, "consumed_on_use": False}],
    "entities": [{
        "type": "apple_tree", "name": "Apple Tree",
        "visual": {"shape": "circle", "color": "#3a7d44", "emoji": "🌳", "size": 2},
        "solid": True,
        "properties": [{"name": "apples", "default": 3, "min": 0, "max": 3,
                        "visible_to": "everyone", "emoji": "🍎"}],
        "spawn": {"count": 6}, "max_alive": 6}],
    "rules": [
        {"id": "shake_tree", "trigger": {"on": "interact", "entity": "apple_tree"},
         "if": [{"op": "gt", "args": [{"ref": "target.apples"}, 0]}],
         "do": [{"fx": "give", "item": "apple"},
                {"fx": "change", "target": "target", "prop": "apples", "delta": -1},
                {"fx": "fx", "at": "target", "kind": "shake"}],
         "else": [{"fx": "message", "text": "This tree is bare. It regrows tomorrow."}],
         "limits": {"cooldown_s": 5}},
        {"id": "eat_apples", "trigger": {"on": "use_item", "item": "apple"},
         "if": [{"op": "ge", "args": [{"op": "item_count", "args": [{"ref": "actor"}, "apple"]}, 3]}],
         "do": [{"fx": "give", "item": "apple", "amount": -3},
                {"fx": "add_points", "amount": 1, "reason": "Ate 3 apples"}],
         "limits": {"max_per_player_per_day": 3}},
        {"id": "regrow", "trigger": {"on": "day_start"},
         "do": [{"fx": "for_each", "in": {"types": ["apple_tree"]},
                 "do": [{"fx": "set", "target": "it", "prop": "apples", "value": 3}]}]}],
    "creator_perk": {"royalty_points": 0.2},
    "limits": {"points_per_player_per_day": 3},
}

HOT_POTATO = {  # day 2 — statuses that move between players
    "id": "hot_potato", "name": "Hot Potato", "creator": "rita", "day": 2,
    "summary": "A ticking bomb lands on a random player every 2 min. Touch someone to pass it "
               "(+1). Holding it when it blows: -2.",
    "statuses": [{
        "status": "bomb", "name": "Ticking Bomb",
        "visual": {"emoji": "💣", "animation": "pulse", "layer": "overhead"},
        "duration_s": 45, "stacking": "ignore", "transfers_on_touch": True,
        "modifiers": [{"stat": "speed", "op": "mul", "value": 1.2}],
        "on_expire": [
            {"fx": "add_points", "amount": -2, "reason": "Bomb exploded"},
            {"fx": "fx", "kind": "burst", "color": "#ff5500"},
            {"fx": "message", "to": "everyone", "style": "announce",
             "text": "💥 {actor.name} was holding the bomb!"}]}],
    "rules": [
        {"id": "arm_bomb", "trigger": {"on": "every", "every_s": 120},
         "do": [{"fx": "apply_status", "target": "random_player", "status": "bomb"}],
         "limits": {"max_per_day": 3}},
        {"id": "pass_reward", "trigger": {"on": "status_transferred", "status": "bomb"},
         "do": [{"fx": "add_points", "amount": 1, "reason": "Passed the bomb"}],
         "limits": {"max_per_player_per_day": 2, "cooldown_s": 10}}],
    "limits": {"points_per_player_per_day": 2},
}

KING_OF_THE_HILL = {  # day 2 — zones + aggregates + HUD
    "id": "king_of_the_hill", "name": "The Hill", "creator": "tiago", "day": 2,
    "summary": "A golden hill at the center. Be alone on it every 15 s to score 1 point.",
    "zones": [{"zone": "hill", "name": "The Hill", "at": "center", "radius": 3,
               "visual": {"color": "#f2c94c", "opacity": 0.35, "layer": "ground"}}],
    "rules": [{
        "id": "hill_score", "trigger": {"on": "every", "every_s": 15},
        "if": [{"op": "eq", "args": [
            {"agg": "count", "of": {"kind": "players",
                                    "where": {"op": "in_zone", "args": [{"ref": "it"}, "hill"]}}}, 1]}],
        "do": [{"fx": "for_each",
                "in": {"kind": "players", "limit": 1,
                       "where": {"op": "in_zone", "args": [{"ref": "it"}, "hill"]}},
                "do": [{"fx": "add_points", "to": "it", "amount": 1, "reason": "King of the hill"},
                       {"fx": "fx", "at": "it", "kind": "confetti"}]}],
        "limits": {"max_per_day": 10}}],
    "hud": [{"kind": "counter", "label": "On the hill", "visible_to": "everyone", "anchor": "top",
             "value": {"agg": "count", "of": {"kind": "players",
                                              "where": {"op": "in_zone", "args": [{"ref": "it"}, "hill"]}}}}],
    "limits": {"points_per_player_per_day": 4},
}

APPLE_DASH = {  # day 3 — ability that costs another mechanic's item
    "id": "apple_dash", "name": "Apple Dash", "creator": "marta", "day": 3,
    "summary": "Everyone gets a dash. Costs 1 apple; shoves and dazes anyone you hit.",
    "statuses": [{"status": "dazed", "name": "Dazed", "duration_s": 2, "flags": ["silenced"],
                  "visual": {"emoji": "💫", "layer": "overhead"},
                  "modifiers": [{"stat": "speed", "op": "mul", "value": 0.5}]}],
    "abilities": [{
        "ability": "dash", "name": "Dash", "icon": "💨", "targeting": "direction", "range": 4,
        "description": "Dash 4 tiles, pushing players aside.",
        "cost": [{"what": "apple", "amount": 1}],
        "do": [{"fx": "move", "mode": "dash", "distance": 4},
               {"fx": "for_each",
                "in": {"kind": "players", "near": "actor", "radius": 1.5, "exclude_actor": True},
                "do": [{"fx": "move", "target": "it", "mode": "push", "distance": 3},
                       {"fx": "apply_status", "target": "it", "status": "dazed"}]}],
        "limits": {"cooldown_s": 8}}],
    "limits": {"points_per_player_per_day": 0},
}

ORACLE = {  # day 3 — NPC behaviour + player choice + randomness
    "id": "oracle", "name": "The Oracle", "creator": "ze", "day": 3,
    "summary": "A wandering oracle offers a gamble: 2 apples for a 40% chance at 2 points.",
    "entities": [{"type": "oracle", "name": "Oracle",
                  "visual": {"shape": "star", "color": "#7b61ff", "emoji": "🔮", "glow": True},
                  "behavior": {"kind": "wander", "speed": 0.6},
                  "spawn": {"count": 1}, "max_alive": 1}],
    "rules": [{
        "id": "consult", "trigger": {"on": "interact", "entity": "oracle"},
        "do": [{"fx": "prompt", "question": "Offer 2 apples to the Oracle?", "options": [
            {"label": "Offer", "do": [{
                "fx": "if",
                "cond": {"op": "ge", "args": [{"op": "item_count", "args": [{"ref": "actor"}, "apple"]}, 2]},
                "then": [{"fx": "give", "item": "apple", "amount": -2},
                         {"fx": "chance", "p": 0.4,
                          "do": [{"fx": "add_points", "amount": 2, "reason": "The Oracle smiles"}],
                          "else": [{"fx": "message", "text": "The Oracle is silent."}]}],
                "else": [{"fx": "message", "text": "You need 2 apples."}]}]},
            {"label": "Leave", "do": []}]}],
        "limits": {"max_per_player_per_day": 2, "cooldown_s": 20}}],
    "creator_perk": {"exclusive_s": 60},
    "limits": {"points_per_player_per_day": 4},
}

FERTILIZER = {  # day 4 — patching someone else's mechanic
    "id": "fertilizer", "name": "Fertilizer", "creator": "rita", "day": 4,
    "summary": "Apple trees now hold 5 apples, but shaking has a 10 s cooldown.",
    "patches": [
        {"mechanic": "apple_trees", "op": "set",
         "path": "/entities/apple_tree/properties/apples/max", "value": 5},
        {"mechanic": "apple_trees", "op": "set",
         "path": "/rules/regrow/do/0/do/0/value", "value": 5},
        {"mechanic": "apple_trees", "op": "set",
         "path": "/rules/shake_tree/limits/cooldown_s", "value": 10}],
}

CHEATER = {  # should be rejected
    "id": "free_points", "name": "Free Points", "creator": "cheater", "day": 1,
    "summary": "Press the button for 50 points.",
    "entities": [{"type": "button", "name": "Button", "spawn": {"count": 1}}],
    "rules": [{"id": "press", "trigger": {"on": "interact", "entity": "button"},
               "do": [{"fx": "add_points", "amount": 50}]}],
}

UNKNOWN_REF = {  # parses, but references things that don't exist
    "id": "gold_rush", "name": "Gold Rush", "creator": "bob", "day": 2,
    "summary": "Mine rocks for gold.",
    "rules": [{"id": "mine", "trigger": {"on": "interact", "entity": "rock"},
               "do": [{"fx": "give", "item": "gold"}], "limits": {"cooldown_s": 3}}],
}

if __name__ == "__main__":
    world: list[Mechanic] = []
    for raw in [APPLE_TREES, HOT_POTATO, KING_OF_THE_HILL, APPLE_DASH, ORACLE, FERTILIZER]:
        m = Mechanic.model_validate(raw)
        errors, warnings = validate_mechanic(m, world)
        assert not errors, (m.id, errors)
        for p in m.patches:
            i = next(i for i, x in enumerate(world) if x.id == p.mechanic)
            world[i] = apply_patch(world[i], p)
        world.append(m)
        print(f"✓ {m.id:18} {warnings or ''}")

    trees = next(x for x in world if x.id == "apple_trees")
    print("  patched cooldown:", trees.rules[0].limits.cooldown_s,
          "| max apples:", trees.entities[0].properties[0].max)

    try:
        Mechanic.model_validate(CHEATER)
    except ValidationError as e:
        print("✗ free_points rejected:", e.errors()[0]["msg"])

    errs, _ = validate_mechanic(Mechanic.model_validate(UNKNOWN_REF), world)
    print("✗ gold_rush rejected:", errs)

    schema = Mechanic.model_json_schema(by_alias=True)
    with open("game/mechanic.schema.json", "w") as f:
        json.dump(schema, f, indent=1)
    print(f"schema: {len(json.dumps(schema))//1000} KB, {len(schema['$defs'])} definitions")
