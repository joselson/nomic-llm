"""All tunables (SPEC §9). Every field can be overridden by an env var
PN_<FIELD_NAME_UPPER>, e.g. PN_PLAY_SECONDS=60. DEBUG=1 enables dev tools."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace

from game.dsl import POINTS_CAP_PER_MECHANIC


@dataclass
class Config:
    map_size: int = 48
    tick_hz: int = 20
    snapshot_hz: int = 10
    day0_seconds: float = 180
    play_seconds: float = 420
    night_seconds: float = 180
    compile_deadline_s: float = 90
    vote_seconds: float = 60
    max_days: int = 20
    win_points: float = 100
    idea_max_chars: int = 150
    author_bonus_points: float = 2
    author_exclusive_s: float = 60
    points_cap_per_mechanic: float = POINTS_CAP_PER_MECHANIC
    max_entities: int = 1000
    max_players: int = 8
    agent_provider: str = "claude_code"   # claude_code | anthropic  (game/pipeline/agents)
    claude_code_bin: str = "claude"
    agent_max_parallel: int = 4           # concurrent CLI processes for the claude_code provider
    compile_model: str = "claude-sonnet-5-5"
    compile_max_attempts: int = 3
    compile_effort: str = "medium"
    compile_fallbacks: bool = True     # server-side refusal fallback (Claude API only)
    sim_seconds: float = 120
    debug: bool = False
    data_dir: str = "data"

    # settings the host may change in the lobby
    HOST_SETTINGS = ("day0_seconds", "play_seconds", "night_seconds", "max_days", "win_points")

    def with_settings(self, settings: dict | None) -> "Config":
        if not settings:
            return self
        changes = {}
        for k, v in settings.items():
            if k in self.HOST_SETTINGS and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                changes[k] = type(getattr(self, k))(v)
        return replace(self, **changes)

    def host_settings(self) -> dict:
        return {k: getattr(self, k) for k in self.HOST_SETTINGS}


def _coerce(raw: str, current):
    if isinstance(current, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def load_config() -> Config:
    cfg = Config()
    for f in fields(cfg):
        raw = os.environ.get(f"PN_{f.name.upper()}")
        if raw is not None:
            setattr(cfg, f.name, _coerce(raw, getattr(cfg, f.name)))
    if os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"):
        cfg.debug = True
    return cfg
