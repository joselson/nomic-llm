"""Rooms, connections and the phase state machine (SPEC §1.1)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import secrets
import time
from dataclasses import dataclass

from fastapi import WebSocket
from pydantic import ValidationError

from game.dsl import CreatorPerk, Mechanic, validate_mechanic
from game.engine.state import PALETTE
from game.engine.world import World
from game.pipeline.night import CompileResult, Night, append_jsonl

from . import protocol as P
from .config import Config

log = logging.getLogger("rooms")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"     # no I / O
NIGHT_SNAPSHOT_EVERY = 10                      # ticks (2 Hz at 20 Hz)


def new_code(rng: random.Random | None = None) -> str:
    r = rng or random.SystemRandom()
    return "".join(r.choice(CODE_ALPHABET) for _ in range(6))


def dumps(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False, default=str)


class Conn:
    """One WebSocket with a bounded outgoing queue drained by its own task."""

    def __init__(self, ws: WebSocket | None) -> None:
        self.ws = ws
        self.q: asyncio.Queue[str] = asyncio.Queue(maxsize=600)
        self.pid: str | None = None
        self.need_tiles = True
        self.closed = False
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._pump())

    def send(self, msg: dict | str) -> None:
        if self.closed:
            return
        try:
            self.q.put_nowait(msg if isinstance(msg, str) else dumps(msg))
        except asyncio.QueueFull:
            log.warning("send queue full for %s; closing", self.pid)
            self.close()

    async def _pump(self) -> None:
        try:
            while True:
                data = await self.q.get()
                await self.ws.send_text(data)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            self.closed = True

    def close(self) -> None:
        self.closed = True
        if self.task is not None:
            self.task.cancel()
        if self.ws is not None:
            asyncio.create_task(self._close_ws())

    async def _close_ws(self) -> None:
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass


@dataclass
class Seat:
    id: str
    name: str
    token: str
    color: str
    connected: bool = True


class JoinError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Room:
    def __init__(self, code: str, cfg: Config, compile_factory=None) -> None:
        self.code = code
        self.base_cfg = cfg
        self.cfg = cfg
        self.phase = "lobby"
        self.stage: str | None = None
        self.day = 0
        self.phase_ends: float | None = None      # monotonic
        self.seats: dict[str, Seat] = {}
        self.conns: dict[str, Conn] = {}
        self.host_id: str | None = None
        self.world: World | None = None
        self.ideas: dict[str, str] = {}
        self.night: Night | None = None
        self.patch_history: list[dict] = []
        self.last_patch_notes: dict[str, dict] = {}
        self.incidents_seen = 0
        self.task: asyncio.Task | None = None
        self.last_activity = time.monotonic()
        self.closed = False
        self._pid = 0
        self.rng = random.Random()
        # compile_factory(room) -> async fn(idea_text) -> CompileResult. Set by main / tests.
        self.compile_factory = compile_factory
        self.ticks = 0

    # ------------------------------------------------------------------ #
    # Connections
    # ------------------------------------------------------------------ #
    def join(self, conn: Conn, name: str, token: str | None) -> str:
        name = re.sub(r"\s+", " ", name).strip()[:16] or "Player"
        seat = next((s for s in self.seats.values() if token and secrets.compare_digest(s.token, token)), None)
        if seat is not None:
            old = self.conns.get(seat.id)
            if old is not None and old is not conn:
                old.send({"t": "error", "code": "replaced", "message": "Connected from another tab"})
                old.close()
            seat.connected = True
        else:
            if self.phase == "game_over":
                raise JoinError("game_over", "This game is over")
            if len(self.seats) >= self.cfg.max_players:
                raise JoinError("room_full", "Room is full")
            self._pid += 1
            pid = f"p{self._pid}"
            used = {s.color for s in self.seats.values()}
            color = next((c for c in PALETTE if c not in used), PALETTE[self._pid % len(PALETTE)])
            taken = {s.name.lower() for s in self.seats.values()}
            base, n = name, 2
            while name.lower() in taken:
                name = f"{base[:13]}{n}"
                n += 1
            seat = Seat(id=pid, name=name, token=secrets.token_urlsafe(16), color=color)
            self.seats[pid] = seat
            if self.host_id is None:
                self.host_id = pid
            if self.world is not None:
                self.world.add_player(pid, seat.name, seat.color)
        conn.pid = seat.id
        conn.need_tiles = True
        self.conns[seat.id] = conn
        if self.world is not None and seat.id in self.world.players:
            self.world.players[seat.id].connected = True
        self.last_activity = time.monotonic()

        conn.send(P.out(P.SWelcome(player_id=seat.id, token=seat.token, code=self.code,
                                   config=self._client_config(), is_host=seat.id == self.host_id)))
        self.broadcast_lobby()
        if self.phase != "lobby":
            conn.send(self._phase_msg())
            if self.world is not None:
                conn.send({"t": "defs", **self.world.defs()})
            if seat.id in self.ideas:
                conn.send({"t": "idea_status", "status": "submitted" if self.phase == "play" else "locked",
                           "text": self.ideas[seat.id]})
            if self.patch_history:
                conn.send(self.last_patch_notes.get(seat.id) or self.patch_history[-1])
            if self.phase == "game_over":
                conn.send(self._game_over_msg())
        return seat.id

    def leave(self, conn: Conn) -> None:
        pid = conn.pid
        if pid is None or self.conns.get(pid) is not conn:
            return
        del self.conns[pid]
        seat = self.seats.get(pid)
        if seat is None:
            return
        seat.connected = False
        if self.phase == "lobby":
            del self.seats[pid]
        elif self.world is not None and pid in self.world.players:
            p = self.world.players[pid]
            p.connected = False
            p.input_dx = p.input_dy = 0.0
            p.input_queue.clear()
        if self.host_id == pid:
            nxt = next((s.id for s in self.seats.values() if s.connected), None)
            if nxt is not None or self.phase == "lobby":
                self.host_id = nxt
        self.last_activity = time.monotonic()
        self.broadcast_lobby()

    def send_to(self, pid: str, msg: dict) -> None:
        c = self.conns.get(pid)
        if c is not None:
            c.send(msg)

    def broadcast(self, msg: dict, pids: list[str] | None = None) -> None:
        data = dumps(msg)
        for pid, c in list(self.conns.items()):
            if pids is None or pid in pids:
                c.send(data)

    def voter_ids(self) -> list[str]:
        return [pid for pid in self.conns if pid in self.seats]

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.broadcast(self._phase_msg())

    def broadcast_lobby(self) -> None:
        players = [P.LobbyPlayer(id=s.id, name=s.name, color=s.color, is_host=s.id == self.host_id,
                                 connected=s.connected) for s in self.seats.values()]
        self.broadcast(P.out(P.SLobby(players=players, settings=self.cfg.host_settings(),
                                      host_id=self.host_id)))

    def _client_config(self) -> dict:
        c = self.cfg
        return {"map_size": c.map_size, "tick_hz": c.tick_hz, "snapshot_hz": c.snapshot_hz,
                "idea_max_chars": c.idea_max_chars, "win_points": c.win_points, "max_days": c.max_days,
                "max_players": c.max_players, "debug": c.debug, **c.host_settings()}

    def _phase_msg(self) -> dict:
        ends_at = None
        if self.phase_ends is not None:
            ends_at = int((time.time() + max(0.0, self.phase_ends - time.monotonic())) * 1000)
        return P.out(P.SPhase(phase=self.phase, day=self.day, ends_at=ends_at, max_days=self.cfg.max_days,
                              stage=self.stage if self.phase == "night" else None))

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #
    def handle(self, pid: str, raw: str) -> None:
        self.last_activity = time.monotonic()
        try:
            msg = P.client_msg.validate_json(raw)
        except ValidationError as e:
            err = e.errors()[0]
            self.send_to(pid, {"t": "error", "code": "bad_message", "message": str(err.get("msg"))[:200]})
            return
        w = self.world
        playing = self.phase == "play" and w is not None
        match msg:
            case P.CInput():
                if playing:
                    w.set_input(pid, msg.dx, msg.dy, msg.facing, msg.seq)
            case P.CInteract():
                if playing:
                    w.queue_action("interact", pid)
            case P.CUseAbility():
                if playing:
                    pt = {"x": msg.point.x, "y": msg.point.y} if msg.point else None
                    w.queue_action("use_ability", pid, msg.ability, msg.target_id, pt, msg.direction)
            case P.CUseItem():
                if playing:
                    w.queue_action("use_item", pid, msg.item)
            case P.CPromptAnswer():
                if playing:
                    w.queue_action("answer_prompt", pid, msg.prompt_id, msg.option)
            case P.CIdea():
                self.on_idea(pid, msg.text)
            case P.CVote():
                err = self.night.cast_vote(pid, msg.idea_id) if self.night else "Voting is not open"
                if err:
                    self.send_to(pid, {"t": "error", "code": "vote", "message": err})
            case P.CChat():
                text = msg.text.strip()
                if text:
                    seat = self.seats.get(pid)
                    self.broadcast({"t": "message", "text": text, "style": "chat", "from": pid,
                                    "name": seat.name if seat else "?"})
                    if playing:
                        w.queue_action("chat", pid, text)
            case P.CStart():
                if pid != self.host_id:
                    self.send_to(pid, {"t": "error", "code": "not_host", "message": "Only the host can start"})
                elif self.phase != "lobby":
                    self.send_to(pid, {"t": "error", "code": "started", "message": "Already started"})
                else:
                    self.start(msg.settings)
            case P.CJoin():
                pass

    def on_idea(self, pid: str, text: str) -> None:
        if self.phase != "play":
            self.send_to(pid, {"t": "error", "code": "ideas_locked", "message": "Ideas are locked until tomorrow"})
            return
        text = re.sub(r"\s+", " ", text).strip()[: self.cfg.idea_max_chars]
        if not text:
            self.ideas.pop(pid, None)
            self.send_to(pid, {"t": "idea_status", "status": "none"})
            return
        self.ideas[pid] = text
        self.send_to(pid, {"t": "idea_status", "status": "submitted", "text": text})

    # ------------------------------------------------------------------ #
    # Phases
    # ------------------------------------------------------------------ #
    def start(self, settings: dict | None = None) -> None:
        self.cfg = self.base_cfg.with_settings(settings)
        c = self.cfg
        self.world = World(map_size=c.map_size, points_cap=c.points_cap_per_mechanic,
                           max_entities=c.max_entities, win_points=c.win_points)
        for s in self.seats.values():
            p = self.world.add_player(s.id, s.name, s.color)
            p.connected = s.id in self.conns
        self.broadcast({"t": "defs", **self.world.defs()})
        self.begin_play(0)
        if self.task is None:
            self.task = asyncio.create_task(self.run_loop())

    def begin_play(self, day: int) -> None:
        self.day = day
        self.phase = "play"
        self.stage = None
        self.ideas = {}
        secs = self.cfg.day0_seconds if day == 0 else self.cfg.play_seconds
        self.phase_ends = time.monotonic() + secs
        self.world.scoring_enabled = day > 0
        self.world.start_day(day, secs)
        self.broadcast(self._phase_msg())
        self.flush_world()

    def end_play(self) -> None:
        w = self.world
        w.end_day()
        self.flush_world()
        if self.day >= self.cfg.max_days:
            self.game_over(self._leaders())
            return
        self.begin_night()

    def begin_night(self) -> None:
        self.phase = "night"
        self.stage = "compile"
        self.phase_ends = time.monotonic() + self.cfg.night_seconds
        for pid, text in self.ideas.items():
            self.send_to(pid, {"t": "idea_status", "status": "locked", "text": text})
        compile_fn = self._compile_fn()
        self.night = Night(self, self.day, dict(self.ideas), compile_fn,
                           compile_deadline_s=self.cfg.compile_deadline_s, vote_seconds=self.cfg.vote_seconds,
                           vote_deadline=self.phase_ends - 15, rng=self.rng)
        self.broadcast(self._phase_msg())
        self.night.start()

    def _compile_fn(self):
        if self.compile_factory is not None:
            return self.compile_factory(self)

        async def offline(text: str) -> CompileResult:
            return CompileResult("infeasible", reason="The idea compiler is offline on this server.")
        return offline

    def end_night(self) -> None:
        night, self.night = self.night, None
        winner = night.finalize() if night else None
        new_day = self.day + 1
        w = self.world
        w.day = new_day
        deployed: Mechanic | None = None
        if winner is not None and winner.mechanic is not None:
            try:
                deployed = self.deploy(winner.mechanic, winner.author, new_day)
            except Exception as e:  # noqa: BLE001
                log.exception("deploy failed")
                winner.status, winner.reason = "infeasible", f"Deploy failed: {e}"
                deployed = None
        if deployed is not None:
            author = w.players.get(winner.author)
            if author is not None:
                w.award_points(author, self.cfg.author_bonus_points, f"Your idea '{deployed.name}' won")
        # patch notes
        incidents = w.incidents[self.incidents_seen:]
        self.incidents_seen = len(w.incidents)
        entries = night.entries if night else []
        win_info = None
        if deployed is not None:
            seat = self.seats.get(winner.author)
            win_info = {"name": deployed.name, "summary": deployed.summary,
                        "author": seat.name if seat else "?", "author_id": winner.author}
        also = [e.mechanic.name for e in entries if e.status == "ok" and e.mechanic is not None
                and (deployed is None or e is not winner)]
        base = {"t": "patch_notes", "day": new_day, "winner": win_info, "also_proposed": also,
                "your_idea": None, "incidents": incidents}
        self.patch_history.append(base)
        self.last_patch_notes = {}
        for e in entries:
            if e is winner and deployed is not None:
                yours = {"status": "won", "reason": None}
            elif e.status == "ok":
                yours = {"status": "lost", "reason": None}
            else:
                yours = {"status": e.status, "reason": e.reason}
            self.last_patch_notes[e.author] = {**base, "your_idea": yours}
        for pid in self.conns:
            self.send_to(pid, self.last_patch_notes.get(pid, base))
        if night is not None:
            append_jsonl(os.path.join(self.cfg.data_dir, "ideas.jsonl"), night.log_records(self.code))
        if deployed is not None:
            self.broadcast({"t": "defs", **w.defs()})
        if w.winner_ids:
            self.game_over(w.winner_ids)
            return
        self.begin_play(new_day)

    def deploy(self, m: Mechanic, author: str, live_day: int) -> Mechanic:
        """Load a mechanic with server-controlled fields (SPEC §4.2.1)."""
        w = self.world
        base = re.sub(r"[^a-z0-9_]", "_", m.id.lower()).strip("_")[:32] or "mechanic"
        if not base[0].isalpha():
            base = "m_" + base
        mid, n = base, 2
        while mid in w.mechanics:
            mid = f"{base[:36]}_{n}"
            n += 1
        m = m.model_copy(update={"id": mid, "creator": author, "day": live_day,
                                 "creator_perk": CreatorPerk(exclusive_s=self.cfg.author_exclusive_s)})
        w.load_mechanic(m)
        self.flush_world()
        return m

    def game_over(self, winner_ids: list[str]) -> None:
        self.phase = "game_over"
        self.stage = None
        self.phase_ends = None
        if self.night is not None:
            self.night.finalize()
            self.night = None
        self.world.winner_ids = list(winner_ids)
        self.broadcast(self._phase_msg())
        self.broadcast(self._game_over_msg())

    def _leaders(self) -> list[str]:
        ps = list(self.world.players.values())
        if not ps:
            return []
        top = max(p.points for p in ps)
        return [p.id for p in ps if p.points == top]

    def _game_over_msg(self) -> dict:
        w = self.world
        standings = sorted(({"id": p.id, "name": p.name, "points": p.points, "color": p.color}
                            for p in w.players.values()), key=lambda r: -r["points"])
        mechanics = [{"name": m.name, "author": (self.seats[m.creator].name if m.creator in self.seats
                                                  else m.creator), "day": m.day, "summary": m.summary}
                     for m in w.mechanics.values()]
        return P.out(P.SGameOver(winner_ids=w.winner_ids, standings=standings, mechanics=mechanics))

    # ------------------------------------------------------------------ #
    # Loop
    # ------------------------------------------------------------------ #
    async def run_loop(self) -> None:
        dt = 1.0 / self.cfg.tick_hz
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        while not self.closed and self.phase != "game_over":
            nxt += dt
            try:
                self.step(dt)
            except Exception:  # noqa: BLE001
                log.exception("room %s step failed", self.code)
            delay = nxt - loop.time()
            if delay < -0.25:
                nxt = loop.time()
                delay = 0
            await asyncio.sleep(max(0.0, delay))

    def step(self, dt: float) -> None:
        w = self.world
        if w is None:
            return
        self.ticks += 1
        now = time.monotonic()
        if self.phase == "play":
            w.tick(dt)
            self.flush_world()
            if self.ticks % max(1, self.cfg.tick_hz // self.cfg.snapshot_hz) == 0:
                self.send_snapshots()
            if w.winner_ids:
                self.game_over(w.winner_ids)
            elif self.phase_ends is not None and now >= self.phase_ends:
                self.end_play()
        elif self.phase == "night":
            if self.ticks % NIGHT_SNAPSHOT_EVERY == 0:
                self.send_snapshots()
            if self.phase_ends is not None and now >= self.phase_ends:
                self.end_night()

    def flush_world(self) -> None:
        if self.world is None:
            return
        for pids, msg in self.world.take_outbox():
            if pids is None:
                self.broadcast(msg)
            else:
                for pid in pids:
                    self.send_to(pid, msg)

    def send_snapshots(self) -> None:
        w = self.world
        common = w.snapshot_common()
        full = None
        for pid, conn in list(self.conns.items()):
            snap = w.snapshot_for(pid, common)
            snap["t"] = "snapshot"
            if conn.need_tiles:
                full = full if full is not None else w.tiles.full()
                snap["tiles_full"] = full
                conn.need_tiles = False
            conn.send(snap)

    # ------------------------------------------------------------------ #
    # Debug tools (DEBUG=1)
    # ------------------------------------------------------------------ #
    def debug_load(self, raw: dict, creator: str | None = None) -> dict:
        if self.world is None:
            raise ValueError("game not started")
        w = self.world
        creator = creator or self.host_id or next(iter(self.seats), "debug")
        data = dict(raw)
        data.setdefault("creator", creator)
        data.setdefault("day", w.day)
        m = Mechanic.model_validate(data)
        errors, warnings = validate_mechanic(m, list(w.mechanics.values()))
        if errors:
            return {"ok": False, "errors": errors, "warnings": warnings}
        m = self.deploy(m, creator, w.day)
        self.broadcast({"t": "defs", **w.defs()})
        return {"ok": True, "id": m.id, "warnings": warnings}

    def debug_skip(self) -> None:
        self.phase_ends = time.monotonic()

    def debug_points(self, pid: str, amount: float) -> None:
        p = self.world.players.get(pid) if self.world else None
        if p is not None:
            self.world.award_points(p, amount, "debug")
            self.flush_world()

    def debug_ff(self, seconds: float) -> None:
        if self.world is None or self.phase != "play":
            return
        dt = 1.0 / self.cfg.tick_hz
        for _ in range(int(seconds * self.cfg.tick_hz)):
            self.world.tick(dt)
            self.flush_world()
            if self.world.winner_ids:
                break
        if self.phase_ends is not None:
            self.phase_ends -= seconds

    def close(self) -> None:
        self.closed = True
        if self.night is not None and self.night.task is not None:
            self.night.task.cancel()
        for c in list(self.conns.values()):
            c.close()


class RoomManager:
    def __init__(self, cfg: Config, compile_factory=None) -> None:
        self.cfg = cfg
        self.rooms: dict[str, Room] = {}
        self.compile_factory = compile_factory

    def create(self) -> Room:
        for _ in range(100):
            code = new_code()
            if code not in self.rooms:
                break
        room = Room(code, self.cfg, self.compile_factory)
        self.rooms[code] = room
        return room

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code.upper())

    def cleanup(self, idle_s: float = 900) -> None:
        now = time.monotonic()
        for code, room in list(self.rooms.items()):
            if not room.conns and now - room.last_activity > idle_s:
                room.close()
                del self.rooms[code]

    async def janitor(self) -> None:
        while True:
            await asyncio.sleep(60)
            self.cleanup()

