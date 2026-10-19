// Client-side store: last known server state + a tiny event bus.
import {
  socket, type Defs, type GameOver, type IdeaStatus, type Lobby, type Me, type PatchNotes, type Phase,
  type RoomConfig, type Snapshot, type HudValue, type VoteStart, type VoteEnd, type NightProgress,
} from "./net/socket";

type Listener = (...args: any[]) => void;

class Bus {
  private ls = new Map<string, Set<Listener>>();
  on(ev: string, fn: Listener): () => void {
    let s = this.ls.get(ev);
    if (!s) this.ls.set(ev, (s = new Set()));
    s.add(fn);
    return () => s!.delete(fn);
  }
  emit(ev: string, ...args: unknown[]) {
    this.ls.get(ev)?.forEach((fn) => {
      try { fn(...args); } catch (e) { console.error(e); }
    });
  }
}

export const bus = new Bus();

export interface TimedSnapshot { at: number; snap: Snapshot }

export const store = {
  code: "",
  playerId: "",
  isHost: false,
  config: null as RoomConfig | null,
  lobby: null as Lobby | null,
  phase: null as Phase | null,
  defs: null as Defs | null,
  snaps: [] as TimedSnapshot[],
  latest: null as Snapshot | null,
  me: null as Me | null,
  hud: [] as HudValue[],
  tiles: new Map<string, string>(),
  patchHistory: [] as PatchNotes[],
  idea: null as IdeaStatus | null,
  vote: null as VoteStart | null,
  myVote: undefined as string | null | undefined,
  voteEnd: null as VoteEnd | null,
  nightProgress: null as NightProgress | null,
  gameOver: null as GameOver | null,
  serverOffset: 0,     // server epoch ms - local epoch ms (approx; assumes synced clocks)
};

export function playerName(id: string | null | undefined): string {
  if (!id) return "";
  return store.latest?.players.find((p) => p.id === id)?.name
    ?? store.lobby?.players.find((p) => p.id === id)?.name ?? "?";
}

export function secondsLeft(): number {
  const p = store.phase;
  if (!p || p.ends_at == null) return 0;
  return Math.max(0, (p.ends_at - Date.now()) / 1000);
}

export function fmtTime(s: number): string {
  s = Math.max(0, Math.ceil(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

let wired = false;

/** Install socket → store handlers once. */
export function wireStore() {
  if (wired) return;
  wired = true;
  socket.on("welcome", (m) => {
    store.code = m.code;
    store.playerId = m.player_id;
    store.isHost = m.is_host;
    store.config = m.config;
    bus.emit("welcome", m);
  });
  socket.on("lobby", (m) => {
    store.lobby = m;
    store.isHost = m.host_id === store.playerId;
    bus.emit("lobby", m);
  });
  socket.on("phase", (m) => {
    const prev = store.phase;
    store.phase = m;
    if (prev?.phase !== m.phase || prev?.day !== m.day) {
      if (m.phase === "night" && prev?.phase !== "night") {
        store.vote = null;
        store.voteEnd = null;
        store.myVote = undefined;
        store.nightProgress = null;
      }
      if (m.phase === "play" && prev?.phase === "night") store.idea = null;
    }
    bus.emit("phase", m, prev);
  });
  socket.on("defs", (m) => {
    store.defs = m;
    bus.emit("defs", m);
  });
  socket.on("snapshot", (m) => {
    if (m.tiles_full) {
      store.tiles.clear();
      for (const [x, y, t] of m.tiles_full) if (t) store.tiles.set(`${x},${y}`, t);
    }
    for (const [x, y, t] of m.tiles_delta) {
      if (t) store.tiles.set(`${x},${y}`, t);
      else store.tiles.delete(`${x},${y}`);
    }
    store.snaps.push({ at: performance.now(), snap: m });
    if (store.snaps.length > 30) store.snaps.splice(0, store.snaps.length - 30);
    store.latest = m;
    store.me = m.me;
    store.hud = m.hud_values;
    bus.emit("snapshot", m);
    if (m.tiles_full || m.tiles_delta.length) bus.emit("tiles");
  });
  socket.on("patch_notes", (m) => {
    if (!store.patchHistory.some((p) => p.day === m.day)) store.patchHistory.push(m);
    else store.patchHistory = store.patchHistory.map((p) => (p.day === m.day ? m : p));
    bus.emit("patch_notes", m);
  });
  socket.on("idea_status", (m) => {
    store.idea = m;
    bus.emit("idea_status", m);
  });
  socket.on("vote_start", (m) => {
    store.vote = m;
    store.myVote = undefined;
    bus.emit("vote_start", m);
  });
  socket.on("vote_end", (m) => {
    store.voteEnd = m;
    bus.emit("vote_end", m);
  });
  socket.on("night_progress", (m) => {
    store.nightProgress = m;
    bus.emit("night_progress", m);
  });
  socket.on("game_over", (m) => {
    store.gameOver = m;
    bus.emit("game_over", m);
  });
  for (const t of ["points", "message", "fx", "prompt", "prompt_close", "error"] as const) {
    socket.on(t, (m) => bus.emit(t, m));
  }
}
