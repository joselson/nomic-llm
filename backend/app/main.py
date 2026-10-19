"""FastAPI app: HTTP + WebSocket routes (SPEC §6)."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from game import dsl_examples

from . import protocol as P
from .config import load_config
from .rooms import Conn, JoinError, RoomManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

cfg = load_config()


def _compile_factory():
    try:
        from game.pipeline.compiler import make_compile_factory
    except ImportError:
        return None
    return make_compile_factory(cfg)


manager = RoomManager(cfg, _compile_factory())


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(manager.janitor())
    yield
    task.cancel()


app = FastAPI(title="Patch Notes", lifespan=lifespan)


@app.post("/api/rooms")
async def create_room():
    return {"code": manager.create().code}


@app.get("/api/rooms/{code}")
async def get_room(code: str):
    room = manager.get(code)
    if room is None:
        return {"exists": False, "phase": None, "players": 0}
    return {"exists": True, "phase": room.phase, "players": len(room.seats)}


@app.websocket("/ws/{code}")
async def ws_endpoint(ws: WebSocket, code: str):
    await ws.accept()
    room = manager.get(code)
    conn = Conn(ws)
    conn.start()
    if room is None:
        conn.send(P.out(P.SError(code="no_room", message="Room not found")))
        await asyncio.sleep(0.1)
        conn.close()
        return
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=15)
        try:
            msg = P.client_msg.validate_json(raw)
        except ValidationError:
            msg = None
        if not isinstance(msg, P.CJoin):
            conn.send(P.out(P.SError(code="join_first", message="Send a join message first")))
            await asyncio.sleep(0.1)
            conn.close()
            return
        try:
            pid = room.join(conn, msg.name, msg.token)
        except JoinError as e:
            conn.send(P.out(P.SError(code=e.code, message=e.message)))
            await asyncio.sleep(0.1)
            conn.close()
            return
        while True:
            raw = await ws.receive_text()
            if len(raw) > 8192:
                continue
            room.handle(pid, raw)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket error")
    finally:
        room.leave(conn)
        conn.close()


# --------------------------------------------------------------------------- #
# Debug tools (DEBUG=1 only)
# --------------------------------------------------------------------------- #
class DebugLoad(BaseModel):
    mechanic: dict | None = None
    example: str | None = None
    creator: str | None = None


class DebugPoints(BaseModel):
    player_id: str
    amount: float


class DebugFF(BaseModel):
    seconds: float


def _debug_room(code: str):
    if not cfg.debug:
        raise HTTPException(404, "debug tools disabled")
    room = manager.get(code)
    if room is None:
        raise HTTPException(404, "room not found")
    return room


EXAMPLES = {name.lower(): getattr(dsl_examples, name) for name in
            ("APPLE_TREES", "HOT_POTATO", "KING_OF_THE_HILL", "APPLE_DASH", "ORACLE", "FERTILIZER")}


@app.get("/api/debug/examples")
async def debug_examples():
    if not cfg.debug:
        raise HTTPException(404, "debug tools disabled")
    return {"examples": list(EXAMPLES)}


@app.post("/api/rooms/{code}/debug/load")
async def debug_load(code: str, body: DebugLoad):
    room = _debug_room(code)
    raw = body.mechanic or EXAMPLES.get((body.example or "").lower())
    if raw is None:
        raise HTTPException(400, f"give 'mechanic' or one of {list(EXAMPLES)} as 'example'")
    try:
        return room.debug_load(raw, body.creator)
    except ValidationError as e:
        return {"ok": False, "errors": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:10]]}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/rooms/{code}/debug/skip")
async def debug_skip(code: str):
    _debug_room(code).debug_skip()
    return {"ok": True}


@app.post("/api/rooms/{code}/debug/points")
async def debug_points(code: str, body: DebugPoints):
    _debug_room(code).debug_points(body.player_id, body.amount)
    return {"ok": True}


@app.post("/api/rooms/{code}/debug/ff")
async def debug_ff(code: str, body: DebugFF):
    _debug_room(code).debug_ff(min(body.seconds, 600))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Built frontend (production / Docker)
# --------------------------------------------------------------------------- #
DIST = os.environ.get("PN_FRONTEND_DIST",
                      os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        root = os.path.realpath(DIST)
        f = os.path.realpath(os.path.join(root, path))
        if path and f.startswith(root + os.sep) and os.path.isfile(f):
            return FileResponse(f)
        return FileResponse(os.path.join(DIST, "index.html"))
