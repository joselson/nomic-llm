// WebSocket client. Message types mirror backend/app/protocol.py — keep in sync.

export type Anchor = "top_left" | "top" | "top_right" | "bottom_left" | "bottom" | "bottom_right";

export interface Visual {
  shape: "circle" | "square" | "triangle" | "diamond" | "hexagon" | "star" | "ring" | "cross" | "none";
  color: string;
  emoji: string | null;
  size: number;
  label: string | null;
  opacity: number;
  outline: string | null;
  glow: boolean;
  animation: "none" | "pulse" | "spin" | "bob" | "shake" | "blink";
  layer: "ground" | "object" | "overhead";
}

// ---- client → server -------------------------------------------------------
export type ClientMsg =
  | { t: "join"; name: string; token?: string | null }
  | { t: "input"; dx: number; dy: number; facing?: number | null; seq: number }
  | { t: "interact" }
  | { t: "use_ability"; ability: string; target_id?: string | null; point?: { x: number; y: number } | null; direction?: number | null }
  | { t: "use_item"; item: string }
  | { t: "idea"; text: string }
  | { t: "vote"; idea_id: string | null }
  | { t: "prompt_answer"; prompt_id: string; option: number }
  | { t: "chat"; text: string }
  | { t: "start"; settings?: Record<string, number> | null };

// ---- server → client -------------------------------------------------------
export interface RoomConfig {
  map_size: number;
  tick_hz: number;
  snapshot_hz: number;
  idea_max_chars: number;
  win_points: number;
  max_days: number;
  max_players: number;
  debug: boolean;
  day0_seconds: number;
  play_seconds: number;
  night_seconds: number;
}

export interface Welcome { t: "welcome"; player_id: string; token: string; code: string; config: RoomConfig; is_host: boolean }
export interface LobbyPlayer { id: string; name: string; color: string; is_host: boolean; connected: boolean }
export interface Lobby { t: "lobby"; players: LobbyPlayer[]; settings: Record<string, number>; host_id: string | null }
export type PhaseName = "lobby" | "play" | "night" | "game_over";
export interface Phase { t: "phase"; phase: PhaseName; day: number; ends_at: number | null; max_days: number; stage: string | null }

export interface EntityDef { name: string; visual: Visual; solid: boolean; interactable: boolean; props: { name: string; label: string | null; emoji: string | null }[] }
export interface ItemDef { name: string; visual: Visual; usable: boolean }
export interface StatusDef { name: string; visual: Visual | null }
export interface AbilityDef {
  id: string; name: string; description: string; icon: string | null;
  targeting: "none" | "self" | "direction" | "point" | "entity" | "player";
  range: number; cost: { what: string; amount: number }[]; cooldown: number | null;
}
export interface MechanicInfo { id: string; name: string; summary: string; creator: string; creator_name: string; day: number; disabled: boolean }
export interface Defs {
  t: "defs";
  map_size: number;
  entities: Record<string, EntityDef>;
  items: Record<string, ItemDef>;
  statuses: Record<string, StatusDef>;
  abilities: AbilityDef[];
  tiles: Record<string, { color: string; walkable: boolean }>;
  zones: Record<string, { name: string; visual: Visual; shape: "circle" | "rect" }>;
  props: Record<string, { label: string; emoji: string | null; visible_to: string }>;
  mechanics: MechanicInfo[];
}

export interface SnapPlayer {
  id: string; name: string; color: string; x: number; y: number; facing: number; points: number;
  hp: number; max_hp: number; size: number; statuses: string[]; visual: Partial<Visual> | null;
  flags: string[]; connected: boolean; dead: boolean;
}
export interface SnapEntity {
  id: string; type: string; x: number; y: number; owner: string | null; hp: number | null; max_hp: number | null;
  props: Record<string, unknown> | null; visual: Partial<Visual> | null; statuses: string[] | null;
}
export interface SnapZone { id: string; zone: string; x: number; y: number; shape: "circle" | "rect"; radius: number; width: number; height: number; owner: string | null }
export interface HudValue { id: string; kind: "counter" | "bar" | "timer" | "text" | "leaderboard" | "icon"; label: string; value: unknown; max: unknown; anchor: Anchor }
export interface Me {
  items: Record<string, number>;
  abilities: { id: string; cooldown: number; cooldown_total: number; uses: number | null; left_today: number | null }[];
  props: Record<string, unknown>;
  ack: number;
  speed: number;
  flags: string[];
  statuses: { id: string; stacks: number; left: number }[];
}
export type TileCell = [number, number, string | null];
export interface Snapshot {
  t: "snapshot"; tick: number; players: SnapPlayer[]; entities: SnapEntity[]; zones: SnapZone[];
  tiles_delta: TileCell[]; tiles_full?: TileCell[] | null; hud_values: HudValue[]; me: Me | null;
}
export interface Points { t: "points"; player_id: string; delta: number; total: number; reason: string | null; mechanic?: string | null }
export interface Message { t: "message"; text: string; style: "toast" | "announce" | "bubble" | "log" | "chat" | "system"; from: string | null; name?: string }
export interface Fx { t: "fx"; kind: "burst" | "ring" | "flash" | "shake" | "trail" | "confetti" | "sound"; x: number; y: number; color: string | null }
export interface Prompt { t: "prompt"; prompt_id: string; question: string; options: string[]; timeout: number; vote: boolean }
export interface PromptClose { t: "prompt_close"; prompt_id: string }
export interface VoteCard { id: string; name: string; summary: string }
export interface VoteStart { t: "vote_start"; ideas: VoteCard[]; ends_at: number; your_idea_id: string | null }
export interface VoteEnd { t: "vote_end"; results: { id: string; name: string; votes: number; winner: boolean }[] }
export interface NightProgress { t: "night_progress"; ready: number; total: number }
export interface PatchNotes {
  t: "patch_notes"; day: number;
  winner: { name: string; summary: string; author: string; author_id?: string } | null;
  also_proposed: string[]; your_idea: { status: string; reason: string | null } | null; incidents: string[];
}
export interface IdeaStatus { t: "idea_status"; status: string; reason?: string | null; text?: string | null }
export interface GameOver {
  t: "game_over"; winner_ids: string[];
  standings: { id: string; name: string; points: number; color: string }[];
  mechanics: { name: string; author: string; day: number; summary?: string }[];
}
export interface ErrorMsg { t: "error"; code: string; message: string }

export type ServerMsg =
  | Welcome | Lobby | Phase | Defs | Snapshot | Points | Message | Fx | Prompt | PromptClose
  | VoteStart | VoteEnd | NightProgress | PatchNotes | IdeaStatus | GameOver | ErrorMsg;
export type ServerType = ServerMsg["t"];
type Handler<T extends ServerType> = (m: Extract<ServerMsg, { t: T }>) => void;

const TOKEN_KEY = (code: string) => `pn_token_${code}`;

export function savedToken(code: string): string | null {
  try { return localStorage.getItem(TOKEN_KEY(code)); } catch { return null; }
}

function saveToken(code: string, token: string) {
  try { localStorage.setItem(TOKEN_KEY(code), token); } catch { /* private mode */ }
}

export class Socket {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<(m: ServerMsg) => void>>();
  private code = "";
  private name = "";
  private token: string | null = null;
  private retries = 0;
  private closedByUs = false;
  onStatus: (s: "open" | "reconnecting" | "closed") => void = () => {};

  on<T extends ServerType>(t: T, fn: Handler<T>): () => void {
    let set = this.handlers.get(t);
    if (!set) this.handlers.set(t, (set = new Set()));
    set.add(fn as (m: ServerMsg) => void);
    return () => set!.delete(fn as (m: ServerMsg) => void);
  }

  /** Connect and join; resolves on `welcome`, rejects on an error before that. */
  connect(code: string, name: string): Promise<Welcome> {
    this.code = code.toUpperCase();
    this.name = name;
    this.token = savedToken(this.code);
    this.closedByUs = false;
    return new Promise((resolve, reject) => {
      let welcomed = false;
      const offW = this.on("welcome", (m) => {
        welcomed = true;
        this.retries = 0;
        this.token = m.token;
        saveToken(this.code, m.token);
        offW();
        offE();
        resolve(m);
      });
      const offE = this.on("error", (m) => {
        if (!welcomed) {
          offW();
          offE();
          this.closedByUs = true;
          this.ws?.close();
          reject(new Error(m.message));
        }
      });
      this.open();
    });
  }

  private open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/${this.code}`);
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ t: "join", name: this.name, token: this.token }));
      this.onStatus("open");
    };
    ws.onmessage = (ev) => {
      let msg: ServerMsg;
      try { msg = JSON.parse(ev.data as string); } catch { return; }
      if (msg.t === "welcome") {
        this.token = msg.token;
        saveToken(this.code, msg.token);
      }
      this.handlers.get(msg.t)?.forEach((fn) => {
        try { fn(msg); } catch (e) { console.error(e); }
      });
    };
    ws.onclose = () => {
      if (this.ws !== ws || this.closedByUs) return;
      if (this.retries++ < 20) {
        this.onStatus("reconnecting");
        setTimeout(() => this.open(), Math.min(5000, 500 * this.retries));
      } else {
        this.onStatus("closed");
      }
    };
  }

  send(msg: ClientMsg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  close() {
    this.closedByUs = true;
    this.ws?.close();
  }
}

export const socket = new Socket();
