"""WebSocket protocol (SPEC §6). Every message is JSON {"t": "<type>", ...}.

Mirrored by hand in frontend/src/net/socket.ts — keep the two in sync.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class M(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --------------------------------------------------------------------------- #
# Client → server
# --------------------------------------------------------------------------- #
class XY(M):
    x: float
    y: float


class CJoin(M):
    t: Literal["join"]
    name: Annotated[str, Field(min_length=1, max_length=16)]
    token: str | None = None


class CInput(M):
    t: Literal["input"]
    dx: Annotated[float, Field(ge=-1, le=1)]
    dy: Annotated[float, Field(ge=-1, le=1)]
    facing: float | None = None
    seq: int = 0


class CInteract(M):
    t: Literal["interact"]


class CUseAbility(M):
    t: Literal["use_ability"]
    ability: str
    target_id: str | None = None
    point: XY | None = None
    direction: float | None = None        # degrees, 0 = east, 90 = south


class CUseItem(M):
    t: Literal["use_item"]
    item: str


class CIdea(M):
    t: Literal["idea"]
    text: Annotated[str, Field(max_length=1000)]


class CVote(M):
    t: Literal["vote"]
    idea_id: str | None = None


class CPromptAnswer(M):
    t: Literal["prompt_answer"]
    prompt_id: str
    option: int


class CChat(M):
    t: Literal["chat"]
    text: Annotated[str, Field(min_length=1, max_length=200)]


class CStart(M):
    t: Literal["start"]
    settings: dict[str, Any] | None = None


ClientMsg = Annotated[Union[CJoin, CInput, CInteract, CUseAbility, CUseItem, CIdea, CVote,
                            CPromptAnswer, CChat, CStart], Field(discriminator="t")]
client_msg = TypeAdapter(ClientMsg)


# --------------------------------------------------------------------------- #
# Server → client
# --------------------------------------------------------------------------- #
class LobbyPlayer(M):
    id: str
    name: str
    color: str
    is_host: bool
    connected: bool


class SWelcome(M):
    t: Literal["welcome"] = "welcome"
    player_id: str
    token: str
    code: str
    config: dict[str, Any]
    is_host: bool


class SLobby(M):
    t: Literal["lobby"] = "lobby"
    players: list[LobbyPlayer]
    settings: dict[str, Any]
    host_id: str | None


class SPhase(M):
    t: Literal["phase"] = "phase"
    phase: Literal["lobby", "play", "night", "game_over"]
    day: int
    ends_at: int | None             # server epoch ms
    max_days: int
    stage: str | None = None        # night sub-stage: compile | vote | results


class SDefs(M):
    t: Literal["defs"] = "defs"
    map_size: int
    entities: dict[str, Any]
    items: dict[str, Any]
    statuses: dict[str, Any]
    abilities: list[dict[str, Any]]
    tiles: dict[str, Any]
    zones: dict[str, Any]
    props: dict[str, Any]
    mechanics: list[dict[str, Any]]


class SnapPlayer(M):
    id: str
    name: str
    color: str
    x: float
    y: float
    facing: float
    points: float
    hp: float
    max_hp: float
    size: float
    statuses: list[str]
    visual: dict[str, Any] | None
    flags: list[str]
    connected: bool
    dead: bool


class SnapEntity(M):
    id: str
    type: str
    x: float
    y: float
    owner: str | None
    hp: float | None
    max_hp: float | None
    props: dict[str, Any] | None
    visual: dict[str, Any] | None
    statuses: list[str] | None


class SnapZone(M):
    id: str
    zone: str
    x: float
    y: float
    shape: str
    radius: float
    width: float
    height: float
    owner: str | None


class SSnapshot(M):
    t: Literal["snapshot"] = "snapshot"
    tick: int
    players: list[SnapPlayer]
    entities: list[SnapEntity]
    zones: list[SnapZone]
    tiles_delta: list[list[Any]]
    tiles_full: list[list[Any]] | None = None
    hud_values: list[dict[str, Any]]
    me: dict[str, Any] | None


class SPoints(M):
    t: Literal["points"] = "points"
    player_id: str
    delta: float
    total: float
    reason: str | None
    mechanic: str | None = None


class SMessage(M):
    t: Literal["message"] = "message"
    text: str
    style: Literal["toast", "announce", "bubble", "log", "chat", "system"]
    from_: str | None = Field(default=None, alias="from")


class SFx(M):
    t: Literal["fx"] = "fx"
    kind: str
    x: float
    y: float
    color: str | None


class SPrompt(M):
    t: Literal["prompt"] = "prompt"
    prompt_id: str
    question: str
    options: list[str]
    timeout: float
    vote: bool = False


class SPromptClose(M):
    t: Literal["prompt_close"] = "prompt_close"
    prompt_id: str


class VoteCard(M):
    id: str
    name: str
    summary: str


class SVoteStart(M):
    t: Literal["vote_start"] = "vote_start"
    ideas: list[VoteCard]
    ends_at: int
    your_idea_id: str | None = None   # so the client can disable self-vote


class SVoteEnd(M):
    t: Literal["vote_end"] = "vote_end"
    results: list[dict[str, Any]]     # [{id, name, votes, winner}]


class SNightProgress(M):
    t: Literal["night_progress"] = "night_progress"
    ready: int
    total: int


class SPatchNotes(M):
    t: Literal["patch_notes"] = "patch_notes"
    day: int
    winner: dict[str, Any] | None     # {name, summary, author}
    also_proposed: list[str]
    your_idea: dict[str, Any] | None  # {status, reason}
    incidents: list[str] = []


class SIdeaStatus(M):
    t: Literal["idea_status"] = "idea_status"
    status: str                       # submitted | locked | compiling | feasible | infeasible | timeout | won | lost
    reason: str | None = None
    text: str | None = None


class SGameOver(M):
    t: Literal["game_over"] = "game_over"
    winner_ids: list[str]
    standings: list[dict[str, Any]]   # [{id, name, points, color}]
    mechanics: list[dict[str, Any]]   # [{name, author, day}]


class SError(M):
    t: Literal["error"] = "error"
    code: str
    message: str


ServerMsg = Annotated[Union[SWelcome, SLobby, SPhase, SDefs, SSnapshot, SPoints, SMessage, SFx, SPrompt,
                            SPromptClose, SVoteStart, SVoteEnd, SNightProgress, SPatchNotes,
                            SIdeaStatus, SGameOver, SError], Field(discriminator="t")]
server_msg = TypeAdapter(ServerMsg)


def out(model: M) -> dict:
    return model.model_dump(by_alias=True)
