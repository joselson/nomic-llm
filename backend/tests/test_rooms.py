"""Room state machine, WebSocket flow and the night pipeline (with a fake compiler)."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.rooms import Conn, Room
from game import dsl_examples as ex
from game.dsl import Mechanic
from game.pipeline.night import CompileResult


class FakeConn(Conn):
    def __init__(self):
        super().__init__(None)
        self.msgs: list[dict] = []

    def send(self, msg):
        self.msgs.append(json.loads(msg) if isinstance(msg, str) else msg)

    def close(self):
        self.closed = True

    def of(self, t):
        return [m for m in self.msgs if m["t"] == t]


def fake_factory(room):
    async def compile_fn(text: str) -> CompileResult:
        await asyncio.sleep(0.01)
        if "3d" in text.lower():
            return CompileResult("infeasible", reason="The world is 2D.")
        raw = dict(ex.KING_OF_THE_HILL if "hill" in text else ex.APPLE_TREES)
        return CompileResult("ok", Mechanic.model_validate(raw), attempts=1)
    return compile_fn


def cfg(**kw):
    base = dict(day0_seconds=1, play_seconds=1, night_seconds=1, compile_deadline_s=1, vote_seconds=1,
                data_dir="/tmp/pn-test-data")
    base.update(kw)
    return Config(**base)


async def room_with(n, **kw):
    room = Room("ABCDEF", cfg(**kw), fake_factory)
    conns = []
    for i in range(n):
        c = FakeConn()
        room.join(c, f"P{i}", None)
        conns.append(c)
    return room, conns


def step(room, seconds):
    for _ in range(int(seconds * 20)):
        room.step(0.05)


async def test_join_lobby_start_and_reconnect():
    room, (a, b) = await room_with(2)
    assert a.of("welcome")[0]["is_host"] and not b.of("welcome")[0]["is_host"]
    assert len(b.of("lobby")[-1]["players"]) == 2
    room.handle("p2", '{"t":"start"}')
    assert b.of("error")[-1]["code"] == "not_host"
    room.handle("p1", '{"t":"start","settings":{"play_seconds":30}}')
    assert room.phase == "play" and room.day == 0 and room.cfg.play_seconds == 30
    assert a.of("phase")[-1]["phase"] == "play"
    # reconnect with token keeps the same player
    token = b.of("welcome")[0]["token"]
    room.leave(b)
    assert not room.world.players["p2"].connected
    b2 = FakeConn()
    assert room.join(b2, "whatever", token) == "p2"
    assert room.world.players["p2"].connected
    assert b2.of("defs") and b2.of("phase")
    room.close()


async def test_full_day_night_cycle_with_vote():
    room, conns = await room_with(3)
    room.start()
    room.handle("p1", '{"t":"idea","text":"apple trees everywhere"}')
    room.handle("p2", '{"t":"idea","text":"a king of the hill"}')
    room.handle("p3", '{"t":"idea","text":"make the game 3D"}')
    assert conns[0].of("idea_status")[-1]["status"] == "submitted"
    room.phase_ends = 0
    room.step(0.05)                     # day 0 ends → night
    assert room.phase == "night"
    room.handle("p1", '{"t":"idea","text":"too late"}')
    assert conns[0].of("error")[-1]["code"] == "ideas_locked"
    for _ in range(50):
        await asyncio.sleep(0.01)
        if room.night.stage == "vote":
            break
    assert room.night.stage == "vote"
    vs = conns[0].of("vote_start")[-1]
    assert len(vs["ideas"]) == 2 and all(set(c) == {"id", "name", "summary"} for c in vs["ideas"])
    infeasible = [m for m in conns[2].of("idea_status") if m["status"] == "infeasible"]
    assert infeasible and infeasible[0]["reason"] == "The world is 2D."
    mine = vs["your_idea_id"]
    other = next(c["id"] for c in vs["ideas"] if c["id"] != mine)
    room.handle("p1", json.dumps({"t": "vote", "idea_id": mine}))
    assert conns[0].of("error")[-1]["code"] == "vote"           # no self-vote
    room.handle("p1", json.dumps({"t": "vote", "idea_id": other}))
    room.handle("p2", json.dumps({"t": "vote", "idea_id": mine}))
    room.handle("p3", json.dumps({"t": "vote", "idea_id": other}))   # all voted → early close
    await asyncio.sleep(0.05)
    assert conns[0].of("vote_end")
    room.phase_ends = 0
    room.step(0.05)                     # night ends → deploy → day 1
    assert room.phase == "play" and room.day == 1
    assert len(room.world.mechanics) == 1
    m = next(iter(room.world.mechanics.values()))
    assert m.creator == "p2" and m.day == 1 and m.creator_perk.exclusive_s == room.cfg.author_exclusive_s
    assert room.world.players["p2"].points == room.cfg.author_bonus_points
    notes = conns[1].of("patch_notes")[-1]
    assert notes["winner"]["author"] == "P1" and notes["your_idea"]["status"] == "won"
    assert conns[2].of("patch_notes")[-1]["your_idea"]["status"] == "infeasible"
    assert conns[0].of("patch_notes")[-1]["your_idea"]["status"] == "lost"
    assert conns[0].of("defs")[-1]["mechanics"][0]["creator"] == "p2"
    room.close()


async def test_single_feasible_idea_wins_without_vote_and_quiet_night():
    room, conns = await room_with(2)
    room.start()
    room.handle("p1", '{"t":"idea","text":"apple trees"}')
    room.phase_ends = 0
    room.step(0.05)
    await asyncio.sleep(0.1)
    assert not conns[0].of("vote_start")
    room.phase_ends = 0
    room.step(0.05)
    assert "apple_trees" in room.world.mechanics
    # next night: no ideas → quiet night
    room.phase_ends = 0
    room.step(0.05)
    await asyncio.sleep(0.05)
    room.phase_ends = 0
    room.step(0.05)
    assert conns[0].of("patch_notes")[-1]["winner"] is None and room.day == 2
    room.close()


async def test_compile_timeout_marks_idea():
    def slow_factory(room):
        async def fn(text):
            await asyncio.sleep(10)
        return fn
    room = Room("ABCDEF", cfg(compile_deadline_s=0.05), slow_factory)
    c = FakeConn()
    room.join(c, "A", None)
    room.start()
    room.handle("p1", '{"t":"idea","text":"anything"}')
    room.phase_ends = 0
    room.step(0.05)
    await asyncio.sleep(0.2)
    assert c.of("idea_status")[-1]["status"] == "timeout"
    room.close()


async def test_game_over_on_win_and_max_days():
    room, conns = await room_with(2, win_points=10)
    room.start()
    room.world.award_points(room.world.players["p2"], 10, "test")
    room.step(0.05)
    assert room.phase == "game_over"
    go = conns[0].of("game_over")[-1]
    assert go["winner_ids"] == ["p2"] and go["standings"][0]["id"] == "p2"
    room.close()
    room, conns = await room_with(2, max_days=0)
    room.start()
    room.phase_ends = 0
    room.step(0.05)
    assert room.phase == "game_over" and set(conns[0].of("game_over")[-1]["winner_ids"]) == {"p1", "p2"}
    room.close()


async def test_debug_load_and_snapshots():
    room, conns = await room_with(1)
    room.start()
    res = room.debug_load(ex.APPLE_TREES)
    assert res["ok"], res
    res = room.debug_load(ex.APPLE_TREES)
    assert not res["ok"]                         # ids already defined
    step(room, 0.2)
    snaps = conns[0].of("snapshot")
    assert snaps and snaps[0]["tiles_full"] is not None and "tiles_full" not in snaps[-1]
    assert len(snaps[-1]["entities"]) == 6
    room.close()


def test_http_and_ws_endpoints(monkeypatch):
    from app import main
    monkeypatch.setattr(main.cfg, "debug", True)
    with TestClient(main.app) as client:
        code = client.post("/api/rooms").json()["code"]
        assert len(code) == 6 and not set(code) & set("IO")
        assert client.get(f"/api/rooms/{code}").json() == {"exists": True, "phase": "lobby", "players": 0}
        assert client.get("/api/rooms/ZZZZZZ").json()["exists"] is False
        with client.websocket_connect(f"/ws/{code}") as ws:
            ws.send_text('{"t":"join","name":"Ana"}')
            assert json.loads(ws.receive_text())["t"] == "welcome"
            ws.send_text('{"t":"start"}')
            r = client.post(f"/api/rooms/{code}/debug/load", json={"example": "apple_trees"})
            assert r.json()["ok"]
            r = client.post(f"/api/rooms/{code}/debug/load", json={"mechanic": {"id": "x"}})
            assert r.json()["ok"] is False
        with client.websocket_connect("/ws/NOROOM") as ws:
            assert json.loads(ws.receive_text())["code"] == "no_room"
        monkeypatch.setattr(main.cfg, "debug", False)
        assert client.post(f"/api/rooms/{code}/debug/skip").status_code == 404


@pytest.fixture(autouse=True)
def _no_stray_tasks():
    yield
