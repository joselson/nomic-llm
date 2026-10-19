"""Night pipeline orchestration (SPEC §5): compile → filter → vote → result.

The room owns the clock: it starts a Night when the play phase ends and calls
finalize() when the night timer runs out, whatever stage the Night reached.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from game.dsl import Mechanic

log = logging.getLogger("night")


@dataclass
class CompileResult:
    status: str                         # ok | infeasible | timeout
    mechanic: Mechanic | None = None
    reason: str | None = None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


CompileFn = Callable[[str], Awaitable[CompileResult]]


@dataclass
class IdeaEntry:
    id: str
    author: str
    text: str
    status: str = "pending"
    mechanic: Mechanic | None = None
    reason: str | None = None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    latency_s: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    votes: int = 0


class RoomIO(Protocol):
    def send_to(self, pid: str, msg: dict) -> None: ...
    def broadcast(self, msg: dict) -> None: ...
    def voter_ids(self) -> list[str]: ...
    def set_stage(self, stage: str) -> None: ...


class Night:
    def __init__(self, io: RoomIO, day: int, ideas: dict[str, str], compile_fn: CompileFn, *,
                 compile_deadline_s: float, vote_seconds: float, vote_deadline: float,
                 rng: random.Random | None = None) -> None:
        self.io = io
        self.day = day
        self.compile_fn = compile_fn
        self.compile_deadline_s = compile_deadline_s
        self.vote_seconds = vote_seconds
        self.vote_deadline = vote_deadline      # monotonic time by which voting must end
        self.rng = rng or random.Random()
        self.entries = [IdeaEntry(id=f"i{n + 1}", author=pid, text=text)
                        for n, (pid, text) in enumerate(ideas.items())]
        self.stage = "compile"
        self.votes: dict[str, str | None] = {}
        self.cards: list[IdeaEntry] = []
        self.winner: IdeaEntry | None = None
        self.decided = False
        self._vote_done = asyncio.Event()
        self.task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        try:
            await self._compile_all()
            self._decide_pool()
            if not self.decided:
                await self._vote()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("night pipeline crashed")
        finally:
            if self.stage != "results":
                self._results()

    async def _compile_all(self) -> None:
        self.io.set_stage("compile")
        total = len(self.entries)
        self.io.broadcast({"t": "night_progress", "ready": 0, "total": total})
        for e in self.entries:
            self.io.send_to(e.author, {"t": "idea_status", "status": "compiling", "text": e.text})
        if not total:
            return
        warm = getattr(self.compile_fn, "warmup", None)
        if warm is not None and total > 1:
            # all ideas share the cached prefix: write it once before the parallel burst
            try:
                await asyncio.wait_for(warm(), timeout=min(15.0, self.compile_deadline_s / 4))
            except Exception:  # noqa: BLE001
                log.warning("compiler warmup skipped")
        ready = 0

        async def one(e: IdeaEntry) -> None:
            nonlocal ready
            t0 = time.monotonic()
            try:
                res = await self.compile_fn(e.text)
            except Exception as ex:  # noqa: BLE001
                log.exception("compile failed")
                res = CompileResult("infeasible", reason=f"The compiler hit an error ({type(ex).__name__}).")
            e.latency_s = round(time.monotonic() - t0, 2)
            e.status, e.mechanic, e.reason = res.status, res.mechanic, res.reason
            e.attempts, e.errors, e.usage = res.attempts, res.errors, res.usage
            ready += 1
            self.io.broadcast({"t": "night_progress", "ready": ready, "total": total})
            if e.status == "ok":
                self.io.send_to(e.author, {"t": "idea_status", "status": "feasible", "text": e.text,
                                           "reason": None})
            else:
                self.io.send_to(e.author, {"t": "idea_status", "status": e.status, "text": e.text,
                                           "reason": e.reason})

        tasks = [asyncio.create_task(one(e)) for e in self.entries]
        done, pending = await asyncio.wait(tasks, timeout=self.compile_deadline_s)
        for t in pending:
            t.cancel()
        for e in self.entries:
            if e.status == "pending":
                e.status, e.reason = "timeout", "The compiler ran out of time on this idea."
                self.io.send_to(e.author, {"t": "idea_status", "status": "timeout", "text": e.text,
                                           "reason": e.reason})

    def feasible(self) -> list[IdeaEntry]:
        return [e for e in self.entries if e.status == "ok" and e.mechanic is not None]

    def _decide_pool(self) -> None:
        pool = self.feasible()
        if len(pool) == 0:
            self.winner, self.decided = None, True
        elif len(pool) == 1:
            self.winner, self.decided = pool[0], True

    async def _vote(self) -> None:
        self.stage = "vote"
        self.io.set_stage("vote")
        self.cards = self.feasible()
        self.rng.shuffle(self.cards)
        remaining = max(5.0, min(self.vote_seconds, self.vote_deadline - time.monotonic()))
        ends_at = int((time.time() + remaining) * 1000)
        cards = [{"id": e.id, "name": e.mechanic.name, "summary": e.mechanic.summary} for e in self.cards]
        by_author = {e.author: e.id for e in self.entries}
        for pid in self.io.voter_ids():
            self.io.send_to(pid, {"t": "vote_start", "ideas": cards, "ends_at": ends_at,
                                  "your_idea_id": by_author.get(pid)})
        try:
            await asyncio.wait_for(self._vote_done.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            pass
        self._tally()

    def cast_vote(self, pid: str, idea_id: str | None) -> str | None:
        """Record a vote. Returns an error string or None."""
        if self.stage != "vote":
            return "Voting is not open"
        card = next((e for e in self.cards if e.id == idea_id), None)
        if idea_id is not None and card is None:
            return "Unknown idea"
        if card is not None and card.author == pid:
            return "You can't vote for your own idea"
        self.votes[pid] = idea_id
        voters = set(self.io.voter_ids())
        if voters and voters <= set(self.votes):
            self._vote_done.set()
        return None

    def _tally(self) -> None:
        if self.decided:
            return
        for e in self.cards:
            e.votes = sum(1 for v in self.votes.values() if v == e.id)
        if self.cards:
            top = max(e.votes for e in self.cards)
            best = [e for e in self.cards if e.votes == top]
            self.winner = self.rng.choice(best)
        self.decided = True
        self.io.broadcast({"t": "vote_end", "results": [
            {"id": e.id, "name": e.mechanic.name, "votes": e.votes, "winner": e is self.winner}
            for e in self.cards]})

    def _results(self) -> None:
        if not self.decided:
            self._decide_pool()
        if not self.decided:
            self.cards = self.cards or self.feasible()
            self._tally()
        self.stage = "results"
        self.io.set_stage("results")

    def finalize(self) -> IdeaEntry | None:
        """Called when the night timer ends: stop whatever is running and return the winner."""
        if self.task is not None and not self.task.done():
            self.task.cancel()
        for e in self.entries:
            if e.status == "pending":
                e.status, e.reason = "timeout", "The compiler ran out of time on this idea."
        if self.stage != "results":
            self._results()
        return self.winner

    # ------------------------------------------------------------------ #
    def log_records(self, room: str) -> list[dict]:
        out = []
        for e in self.entries:
            out.append({
                "ts": time.time(), "room": room, "day": self.day, "author": e.author, "idea": e.text,
                "outcome": e.status, "attempts": e.attempts, "reason": e.reason, "errors": e.errors,
                "mechanic": e.mechanic.model_dump(by_alias=True, exclude_defaults=True) if e.mechanic else None,
                "votes": e.votes, "won": e is self.winner, "latency_s": e.latency_s, "usage": e.usage,
            })
        return out


def append_jsonl(path: str, records: list[dict]) -> None:
    if not records:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    except OSError:
        log.exception("could not write %s", path)
