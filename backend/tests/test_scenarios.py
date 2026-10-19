"""Golden scenarios for the example mechanics (SPEC §7 milestone 2)."""
from game import dsl_examples as ex

from .helpers import entities_of, make_world, park_players, place, run


def test_all_examples_load_together():
    w = make_world(ex.APPLE_TREES, ex.HOT_POTATO, ex.KING_OF_THE_HILL, ex.APPLE_DASH, ex.ORACLE,
                   ex.FERTILIZER, players=4)
    assert set(w.mechanics) == {"apple_trees", "hot_potato", "king_of_the_hill", "apple_dash", "oracle",
                                "fertilizer"}
    assert len(entities_of(w, "apple_tree")) == 6
    assert len(entities_of(w, "oracle")) == 1
    assert any(z.zone == "hill" for z in w.zones.values())
    run(w, 30)   # random-ish play must not raise or disable anything
    assert not w.disabled_rules


def _next_to_tree(w, pid="p1"):
    park_players(w)
    tree = entities_of(w, "apple_tree")[0]
    place(w, pid, tree.x + tree.radius + 0.6, tree.y)
    return tree


def test_shake_tree_gives_apple_with_cooldown():
    w = make_world(ex.APPLE_TREES)
    tree = _next_to_tree(w)
    p = w.players["p1"]
    w.queue_action("interact", "p1")
    run(w, 0.1)
    assert p.items.get("apple") == 1
    assert tree.props["apples"] == 2
    w.queue_action("interact", "p1")      # cooldown 5 s
    run(w, 0.1)
    assert p.items.get("apple") == 1
    run(w, 5)
    w.queue_action("interact", "p1")
    run(w, 0.1)
    assert p.items.get("apple") == 2


def test_bare_tree_runs_else_branch():
    w = make_world(ex.APPLE_TREES)
    tree = _next_to_tree(w)
    tree.props["apples"] = 0
    w.take_outbox()
    w.queue_action("interact", "p1")
    run(w, 0.1)
    assert "apple" not in w.players["p1"].items
    texts = [m["text"] for _, m in w.take_outbox() if m["t"] == "message"]
    assert any("bare" in t for t in texts)


def test_regrow_on_day_start():
    w = make_world(ex.APPLE_TREES)
    for t in entities_of(w, "apple_tree"):
        t.props["apples"] = 0
    w.end_day()
    w.start_day(2, 420)
    assert all(t.props["apples"] == 3 for t in entities_of(w, "apple_tree"))


def test_eat_three_apples_for_a_point_capped_at_three_per_day():
    w = make_world(ex.APPLE_TREES)
    p = w.players["p1"]
    p.items["apple"] = 15
    for _ in range(5):
        w.queue_action("use_item", "p1", "apple")
        run(w, 0.1)
    assert p.points == 3            # max_per_player_per_day = 3 and points cap = 3
    assert p.items["apple"] == 6    # only 3 successful meals consumed apples
    w.end_day()
    w.start_day(2, 420)
    w.queue_action("use_item", "p1", "apple")
    run(w, 0.1)
    assert p.points == 4


def test_eating_needs_three_apples():
    w = make_world(ex.APPLE_TREES)
    p = w.players["p1"]
    p.items["apple"] = 2
    w.queue_action("use_item", "p1", "apple")
    run(w, 0.1)
    assert p.points == 0 and p.items["apple"] == 2


def test_bomb_transfers_on_touch_and_rewards_the_passer():
    w = make_world(ex.HOT_POTATO, players=3)
    park_players(w)
    a, b = w.players["p1"], w.players["p2"]
    w.apply_status(a, "bomb")
    assert [s.status for s in a.statuses] == ["bomb"]
    place(w, "p1", 20, 20)
    place(w, "p2", 20.75, 20)            # touching (radius 0.4 each)
    run(w, 0.1)
    assert not a.statuses and [s.status for s in b.statuses] == ["bomb"]
    assert a.points == 1                 # pass_reward: actor = giver
    run(w, 1)                            # transfer cooldown 3 s: no instant pass back
    assert [s.status for s in b.statuses] == ["bomb"] and not a.statuses


def test_bomb_explodes_on_holder():
    w = make_world(ex.HOT_POTATO, players=2)
    park_players(w)
    b = w.players["p2"]
    b.points = 5
    w.apply_status(b, "bomb")
    run(w, 46)
    assert not b.statuses
    assert b.points == 3


def test_bomb_arms_every_two_minutes():
    w = make_world(ex.HOT_POTATO, players=3)
    park_players(w)
    run(w, 120.1)
    assert sum(1 for p in w.players.values() for s in p.statuses if s.status == "bomb") == 1


def test_hill_scores_only_when_alone():
    w = make_world(ex.KING_OF_THE_HILL, players=3)
    park_players(w)
    c = w.map_size / 2
    place(w, "p1", c, c)
    run(w, 15.1)
    assert w.players["p1"].points == 1
    place(w, "p2", c + 1.5, c)
    run(w, 15)
    assert w.players["p1"].points == 1 and w.players["p2"].points == 0


def test_hill_hud_counts_players():
    w = make_world(ex.KING_OF_THE_HILL, players=2)
    park_players(w)
    c = w.map_size / 2
    place(w, "p1", c, c)
    run(w, 0.1)
    hud = w.hud_values("p2")
    assert hud[0]["label"] == "On the hill" and hud[0]["value"] == 1


def test_fertilizer_patch_changes_cooldown_and_capacity():
    w = make_world(ex.APPLE_TREES, ex.FERTILIZER)
    trees = w.mechanics["apple_trees"]
    assert trees.rules[0].limits.cooldown_s == 10
    tree = _next_to_tree(w)
    p = w.players["p1"]
    w.queue_action("interact", "p1")
    run(w, 0.1)
    assert p.items["apple"] == 1
    run(w, 6)
    w.queue_action("interact", "p1")
    run(w, 0.1)
    assert p.items["apple"] == 1          # still cooling down (10 s now)
    run(w, 4.5)
    w.queue_action("interact", "p1")
    run(w, 0.1)
    assert p.items["apple"] == 2
    w.end_day()
    w.start_day(2, 420)
    assert tree.props["apples"] == 5      # regrow patched to 5 (max patched to 5)


def test_apple_dash_costs_an_apple_and_pushes_people():
    w = make_world(ex.APPLE_TREES, ex.APPLE_DASH, players=2)
    park_players(w)
    a, b = w.players["p1"], w.players["p2"]
    place(w, "p1", 10, 10)
    place(w, "p2", 11.2, 10)
    w.queue_action("use_ability", "p1", "dash", None, None, 0.0)   # no apple: refused
    run(w, 0.1)
    assert a.x < 10.5
    a.items["apple"] = 1
    w.queue_action("use_ability", "p1", "dash", None, None, 0.0)
    run(w, 1.0)
    assert "apple" not in a.items
    assert a.x > 12
    assert any(s.status == "dazed" for s in b.statuses) or b.x > 12


def test_oracle_prompt_and_gamble():
    w = make_world(ex.APPLE_TREES, ex.ORACLE, players=2, seed=3)
    park_players(w)
    oracle = entities_of(w, "oracle")[0]
    place(w, "p1", oracle.x + 1.0, oracle.y)
    w.players["p1"].items["apple"] = 2
    w.take_outbox()
    w.queue_action("interact", "p1")
    run(w, 0.05)
    prompts = [m for pids, m in w.take_outbox() if m["t"] == "prompt"]
    assert prompts and prompts[0]["options"] == ["Offer", "Leave"]
    w.queue_action("answer_prompt", "p1", prompts[0]["prompt_id"], 0)
    run(w, 0.05)
    assert "apple" not in w.players["p1"].items
    assert w.players["p1"].points in (0, 2)
