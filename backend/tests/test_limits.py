from game.dsl import Limits
from game.engine.limits import Limiter

from .helpers import make_world, run

POINTS = {
    "id": "pts", "name": "Pts", "summary": "s",
    "statuses": [{"status": "double", "name": "Double", "duration_s": 60,
                  "modifiers": [{"stat": "points_mult", "op": "mul", "value": 2}]}],
    "rules": [{"id": "r", "trigger": {"on": "custom", "event": "go"},
               "do": [{"fx": "add_points", "amount": {"ref": "event.n"}}], "limits": {"cooldown_s": 0}}],
    "limits": {"points_per_player_per_day": 4, "points_loss_per_player_per_day": 2},
}


def test_limiter_counters():
    lim = Limiter()
    L = Limits(cooldown_s=5, max_per_player_per_day=2, max_total=3)
    assert lim.check("k", L, "p", 0)
    lim.consume("k", "p", 0)
    assert not lim.check("k", L, "p", 1)          # cooldown
    assert lim.check("k", L, "p", 1, cd_mult=0.1)  # cooldown_mult shortens it
    assert lim.check("k", L, "p", 6)
    lim.consume("k", "p", 6)
    assert not lim.check("k", L, "p", 20)         # per player per day
    assert lim.check("k", L, "q", 20)             # other player fine
    lim.consume("k", "q", 20)
    lim.reset_day()
    assert not lim.check("k", L, "q", 100)        # max_total = 3 reached
    G = Limits(global_cooldown_s=10, max_per_day=1)
    lim.consume("g", "a", 0)
    assert not lim.check("g", G, "b", 5)
    lim.reset_day()
    assert lim.check("g", G, "b", 11)
    assert lim.cooldown_left("k", L, "q", 22) == 3


def _go(w, pid, n):
    from game.engine.events import Event
    w.push(Event("custom", w.players[pid], None, {"event": "go", "n": n}))
    w.queue.drain()


def test_ledger_caps_mult_and_floor():
    w = make_world(POINTS)
    p = w.players["p1"]
    _go(w, "p1", 3)
    _go(w, "p1", 3)
    assert p.points == 4                          # daily gain cap 4
    _go(w, "p1", -5)
    assert p.points == 2                          # daily loss cap 2
    w.end_day()
    w.start_day(2, 420)
    w.apply_status(p, "double")
    _go(w, "p1", 1.5)
    assert p.points == 5                          # 1.5 × 2
    p.points = 0.5
    _go(w, "p1", -1)
    assert p.points == 0                          # floor at 0


def test_scoring_disabled_on_day_zero():
    w = make_world(POINTS, day=0)
    w.scoring_enabled = False
    _go(w, "p1", 3)
    assert w.players["p1"].points == 0


def test_win_detection():
    w = make_world(POINTS, win_points=10)
    p = w.players["p1"]
    p.points = 8
    _go(w, "p1", 3)
    assert w.winner_ids == ["p1"] and p.points == 11
    run(w, 0.05)
