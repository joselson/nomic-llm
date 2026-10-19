"""Per-rule limit counters and the points ledger (SPEC §4.7)."""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from game.dsl import Limits

if TYPE_CHECKING:
    from .state import Player
    from .world import World


class Limiter:
    """Counters keyed by (mechanic/rule key, player|None). Day counters reset at day_start."""

    def __init__(self) -> None:
        self.last_use: dict[tuple[str, str | None], float] = {}
        self.last_global: dict[str, float] = {}
        self.day_player: dict[tuple[str, str | None], int] = defaultdict(int)
        self.day_global: dict[str, int] = defaultdict(int)
        self.total: dict[str, int] = defaultdict(int)

    def reset_day(self) -> None:
        self.day_player.clear()
        self.day_global.clear()

    def check(self, key: str, lim: Limits, pid: str | None, now: float, cd_mult: float = 1.0) -> bool:
        if lim.cooldown_s is not None:
            last = self.last_use.get((key, pid))
            if last is not None and now - last < lim.cooldown_s * cd_mult:
                return False
        if lim.global_cooldown_s is not None:
            last = self.last_global.get(key)
            if last is not None and now - last < lim.global_cooldown_s:
                return False
        if lim.max_per_player_per_day is not None and self.day_player[(key, pid)] >= lim.max_per_player_per_day:
            return False
        if lim.max_per_day is not None and self.day_global[key] >= lim.max_per_day:
            return False
        if lim.max_total is not None and self.total[key] >= lim.max_total:
            return False
        return True

    def consume(self, key: str, pid: str | None, now: float) -> None:
        self.last_use[(key, pid)] = now
        self.last_global[key] = now
        self.day_player[(key, pid)] += 1
        self.day_global[key] += 1
        self.total[key] += 1

    def cooldown_left(self, key: str, lim: Limits, pid: str | None, now: float, cd_mult: float = 1.0) -> float:
        left = 0.0
        if lim.cooldown_s is not None and (key, pid) in self.last_use:
            left = max(left, lim.cooldown_s * cd_mult - (now - self.last_use[(key, pid)]))
        if lim.global_cooldown_s is not None and key in self.last_global:
            left = max(left, lim.global_cooldown_s - (now - self.last_global[key]))
        return max(0.0, left)

    def uses_left_today(self, key: str, lim: Limits, pid: str | None) -> int | None:
        opts = []
        if lim.max_per_player_per_day is not None:
            opts.append(lim.max_per_player_per_day - self.day_player[(key, pid)])
        if lim.max_per_day is not None:
            opts.append(lim.max_per_day - self.day_global[key])
        if lim.max_total is not None:
            opts.append(lim.max_total - self.total[key])
        return max(0, min(opts)) if opts else None


class Ledger:
    """Every add_points goes through here: multiplier, per-mechanic daily caps, floor at 0."""

    def __init__(self, world: "World") -> None:
        self.world = world
        self.gained: dict[tuple[str, str, int], float] = defaultdict(float)
        self.lost: dict[tuple[str, str, int], float] = defaultdict(float)

    def apply(self, mechanic_id: str, player: "Player", amount: float, reason: str | None = None) -> float:
        w = self.world
        if amount == 0 or not w.scoring_enabled:
            return 0.0
        m = w.mechanics.get(mechanic_id)
        cap = w.points_cap
        gain_cap = min(cap, m.limits.points_per_player_per_day) if m else cap
        loss_cap = min(cap, m.limits.points_loss_per_player_per_day) if m else cap
        key = (mechanic_id, player.id, w.day)
        if amount > 0:
            amount *= max(0.0, player.stats.get("points_mult", 1.0))
            amount = min(amount, max(0.0, gain_cap - self.gained[key]))
        else:
            amount = -min(-amount, max(0.0, loss_cap - self.lost[key]))
        amount = round(amount, 2)
        # floor the player's total at 0
        if player.points + amount < 0:
            amount = -player.points
        if amount == 0:
            return 0.0
        if amount > 0:
            self.gained[key] += amount
        else:
            self.lost[key] += -amount
        w.change_points(player, amount, reason, mechanic_id)
        return amount

    def gained_today(self, mechanic_id: str, pid: str) -> float:
        return self.gained[(mechanic_id, pid, self.world.day)]
