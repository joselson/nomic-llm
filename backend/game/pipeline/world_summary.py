"""Compact description of the live world for the compile agent (SPEC §5.4)."""
from __future__ import annotations

import json

from game.dsl import Mechanic

MAX_CHARS = 12000          # ~3k tokens


def world_summary(mechanics: list[Mechanic], *, day: int, player_count: int, map_size: int) -> str:
    reg: dict[str, list] = {"items": [], "entities": [], "statuses": [], "abilities": [], "zones": [],
                            "tiles": [], "player_props": [], "world_props": [], "entity_props": [],
                            "custom_events": []}
    for m in mechanics:
        reg["items"] += [{"id": i.item, "name": i.name, "usable": i.usable} for i in m.items]
        reg["entities"] += [{"type": e.type, "name": e.name, "props": [p.name for p in e.properties],
                             "tags": e.tags} for e in m.entities]
        reg["statuses"] += [{"id": s.status, "name": s.name} for s in m.statuses]
        reg["abilities"] += [{"id": a.ability, "name": a.name, "available_to": a.available_to}
                             for a in m.abilities]
        reg["zones"] += [{"id": z.zone, "name": z.name} for z in m.zones]
        reg["tiles"] += [t.tile for t in m.tiles]
        reg["player_props"] += [p.name for p in m.player_props]
        reg["world_props"] += [p.name for p in m.world_props]
        reg["entity_props"] += [p.name for e in m.entities for p in e.properties]
        reg["custom_events"] += sorted({fx.event for fx in _emits(m)})
    reg = {k: v for k, v in reg.items() if v}
    active = [{"id": m.id, "name": m.name, "summary": m.summary, "day": m.day,
               "rules": [r.id for r in m.rules], **({"disabled": True} if m.disabled else {})}
              for m in mechanics]
    head = {"day": day, "players": player_count, "map_size": map_size}

    def render(items: list) -> str:
        return json.dumps({**head, "active_mechanics": items, "existing_ids": reg},
                          ensure_ascii=False, separators=(",", ":"))

    out = render(active)
    # truncate the oldest summaries first, then drop them entirely
    i = 0
    while len(out) > MAX_CHARS and i < len(active):
        active[i] = {k: v for k, v in active[i].items() if k != "summary"} | {"summary": "(omitted)"}
        out = render(active)
        i += 1
    while len(out) > MAX_CHARS and active:
        active.pop(0)
        out = render(active)
    return out


def _emits(m: Mechanic):
    from game.dsl import EmitFx, _walk
    return (n for n in _walk(m) if isinstance(n, EmitFx))
