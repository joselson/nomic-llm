import Phaser from "phaser";
import { socket, type Fx, type Message, type Points, type SnapEntity, type SnapPlayer, type Snapshot } from "../net/socket";
import { axis, clearKeys } from "../input/keys";
import { INPUT_DT, PLAYER_RADIUS, Predictor, type Collider } from "../render/predict";
import { EntitySprite, PlayerSprite } from "../render/sprites";
import { DEPTH, TEXT_FONT, TILE, hex, mergeVisual } from "../render/visuals";
import { bus, store } from "../state";
import { isTyping } from "../ui/dom";
import { mountWorldUI } from "../ui";

const INTERP_MS = 110;

interface Bubble { from: string; text: Phaser.GameObjects.Text; until: number }

export class WorldScene extends Phaser.Scene {
  private players = new Map<string, PlayerSprite>();
  private entities = new Map<string, EntitySprite>();
  private tilesG!: Phaser.GameObjects.Graphics;
  private zonesG!: Phaser.GameObjects.Graphics;
  private zoneLabels: Phaser.GameObjects.Text[] = [];
  private zoneKey = "";
  private border!: Phaser.GameObjects.Graphics;
  private pred = new Predictor();
  private disp = { x: 0, y: 0 };
  private facing = 0;
  private inputAcc = 0;
  private lastSentZero = false;
  private bubbles: Bubble[] = [];
  private offs: (() => void)[] = [];
  private follow!: Phaser.GameObjects.Zone;
  private interactTarget: string | null = null;

  constructor() {
    super("world");
  }

  create() {
    const cam = this.cameras.main;
    cam.setBackgroundColor("#ffffff");
    this.tilesG = this.add.graphics().setDepth(DEPTH.tiles);
    this.zonesG = this.add.graphics().setDepth(DEPTH.zones);
    this.border = this.add.graphics().setDepth(DEPTH.tiles);
    this.follow = this.add.zone(0, 0, 1, 1);
    this.drawBorder();
    cam.startFollow(this.follow, true, 0.15, 0.15);
    this.fitZoom();
    this.scale.on("resize", this.fitZoom, this);

    this.offs.push(
      bus.on("snapshot", (s: Snapshot) => this.onSnapshot(s)),
      bus.on("tiles", () => this.drawTiles()),
      bus.on("defs", () => {
        this.drawBorder();
        this.zoneKey = "";
      }),
      bus.on("points", (m: Points) => this.floatText(m.player_id, `${m.delta > 0 ? "+" : ""}${m.delta}`, m.delta > 0 ? "#2e9e5b" : "#d64545")),
      bus.on("message", (m: Message) => {
        if (m.style === "bubble" && m.from) this.bubble(m.from, m.text);
        if (m.style === "chat" && m.from) this.bubble(m.from, m.text);
      }),
      bus.on("fx", (m: Fx) => this.fx(m)),
      bus.on("action:ability", (id: string) => this.useAbility(id)),
      bus.on("action:item", (id: string) => socket.send({ t: "use_item", item: id })),
      bus.on("action:interact", () => socket.send({ t: "interact" })),
      bus.on("phase", () => clearKeys()),
    );
    const onKey = (e: KeyboardEvent) => this.onKey(e);
    window.addEventListener("keydown", onKey);
    this.offs.push(() => window.removeEventListener("keydown", onKey));
    this.offs.push(mountWorldUI());
    this.drawTiles();
    if (store.latest) this.onSnapshot(store.latest);

    this.events.once("shutdown", () => {
      this.offs.forEach((f) => f());
      this.scale.off("resize", this.fitZoom, this);
    });
  }

  // ------------------------------------------------------------------ //
  private mapSize(): number {
    return store.defs?.map_size ?? store.config?.map_size ?? 48;
  }

  private fitZoom() {
    const w = this.scale.width;
    const h = this.scale.height;
    const z = Phaser.Math.Clamp(Math.min(w / (26 * TILE), h / (16 * TILE)), 0.6, 2);
    this.cameras.main.setZoom(z);
    const m = this.mapSize() * TILE;
    const pad = 4 * TILE;
    this.cameras.main.setBounds(-pad, -pad, m + pad * 2, m + pad * 2);
  }

  private drawBorder() {
    const m = this.mapSize() * TILE;
    this.border.clear();
    this.border.lineStyle(2, 0xd9d9de, 1).strokeRect(0, 0, m, m);
  }

  private drawTiles() {
    const g = this.tilesG;
    g.clear();
    const defs = store.defs;
    for (const [k, t] of store.tiles) {
      const [x, y] = k.split(",").map(Number);
      g.fillStyle(hex(defs?.tiles[t]?.color, 0xcccccc), 1).fillRect(x * TILE, y * TILE, TILE, TILE);
    }
  }

  private drawZones(s: Snapshot) {
    const key = JSON.stringify(s.zones) + (store.defs?.mechanics.length ?? 0);
    if (key === this.zoneKey) return;
    this.zoneKey = key;
    const g = this.zonesG;
    g.clear();
    this.zoneLabels.forEach((t) => t.destroy());
    this.zoneLabels = [];
    for (const z of s.zones) {
      const def = store.defs?.zones[z.zone];
      const v = mergeVisual({ opacity: 0.3, layer: "ground", color: "#4363d8" }, def?.visual);
      const color = hex(v.color);
      g.fillStyle(color, v.opacity);
      g.lineStyle(2, color, Math.min(1, v.opacity + 0.35));
      if (z.shape === "rect") {
        g.fillRect((z.x - z.width / 2) * TILE, (z.y - z.height / 2) * TILE, z.width * TILE, z.height * TILE);
        g.strokeRect((z.x - z.width / 2) * TILE, (z.y - z.height / 2) * TILE, z.width * TILE, z.height * TILE);
      } else {
        g.fillCircle(z.x * TILE, z.y * TILE, z.radius * TILE);
        g.strokeCircle(z.x * TILE, z.y * TILE, z.radius * TILE);
      }
      const top = z.shape === "rect" ? z.y - z.height / 2 : z.y - z.radius;
      const label = `${v.emoji ? v.emoji + " " : ""}${def?.name ?? z.zone}`;
      this.zoneLabels.push(this.add.text(z.x * TILE, top * TILE + 4, label, {
        fontFamily: TEXT_FONT, fontSize: "12px", color: "#333",
      }).setOrigin(0.5, 0).setAlpha(0.75).setDepth(DEPTH.zones));
    }
  }

  // ------------------------------------------------------------------ //
  private colliders(): Collider[] {
    const s = store.latest;
    const defs = store.defs;
    const out: Collider[] = [];
    if (!s || !defs) return out;
    for (const e of s.entities) {
      const d = defs.entities[e.type];
      if (!d?.solid) continue;
      const v = mergeVisual(d.visual, e.visual);
      out.push({ x: e.x, y: e.y, r: v.size / 2, square: v.shape === "square" });
    }
    for (const p of s.players) {
      if (p.id === store.playerId || p.dead || !p.connected || p.flags.includes("ghost")) continue;
      out.push({ x: p.x, y: p.y, r: PLAYER_RADIUS * p.size, square: false });
    }
    return out;
  }

  private myServer(): SnapPlayer | undefined {
    return store.latest?.players.find((p) => p.id === store.playerId);
  }

  private onSnapshot(s: Snapshot) {
    const me = s.players.find((p) => p.id === store.playerId);
    if (me && s.me) {
      if (!this.pred.ready || me.dead) {
        this.pred.reset(me.x, me.y);
        this.disp = { x: me.x, y: me.y };
      } else {
        this.pred.reconcile(me.x, me.y, s.me.ack, s.me.speed, s.me.flags, this.colliders(), this.mapSize(), me.size);
      }
    }
    // create / destroy sprites
    const seenP = new Set<string>();
    for (const p of s.players) {
      seenP.add(p.id);
      if (!this.players.has(p.id)) this.players.set(p.id, new PlayerSprite(this, p.id, p.id === store.playerId));
    }
    for (const [id, sp] of this.players) if (!seenP.has(id)) { sp.destroy(); this.players.delete(id); }
    const seenE = new Set<string>();
    for (const e of s.entities) {
      seenE.add(e.id);
      if (!this.entities.has(e.id)) this.entities.set(e.id, new EntitySprite(this, e.id, e, store.defs));
    }
    for (const [id, sp] of this.entities) if (!seenE.has(id)) { sp.destroy(); this.entities.delete(id); }
    this.drawZones(s);
  }

  // ------------------------------------------------------------------ //
  update(_time: number, delta: number) {
    const s = store.latest;
    if (!s) return;
    const playing = store.phase?.phase === "play";
    const meSnap = store.me;
    const meServer = this.myServer();

    // local input → commands at 20 Hz
    this.inputAcc = Math.min(this.inputAcc + delta / 1000, 0.25);
    while (this.inputAcc >= INPUT_DT) {
      this.inputAcc -= INPUT_DT;
      if (!playing || !meSnap || !meServer || meServer.dead) continue;
      const [dx, dy] = axis();
      const zero = dx === 0 && dy === 0;
      if (zero && this.lastSentZero) continue;    // idle: no need to stream zeros
      this.lastSentZero = zero;
      const seq = this.pred.step(dx, dy, meSnap.speed, meSnap.flags, this.colliders(), this.mapSize(), meServer.size);
      socket.send({ t: "input", dx, dy, seq });
      if (!zero && !meSnap.flags.includes("frozen")) {
        const rev = meSnap.flags.includes("reversed_controls") ? -1 : 1;
        this.facing = (Math.atan2(dy * rev, dx * rev) * 180) / Math.PI;
      }
    }

    // smooth display position toward the prediction
    if (this.pred.ready) {
      const k = Math.min(1, (delta / 1000) * 18);
      const ex = this.pred.x - this.disp.x;
      const ey = this.pred.y - this.disp.y;
      if (Math.hypot(ex, ey) > 2) this.disp = { x: this.pred.x, y: this.pred.y };
      else this.disp = { x: this.disp.x + ex * k, y: this.disp.y + ey * k };
    }

    // interpolate everyone else
    const { a, b, t } = this.interpFrames();
    const pa = new Map(a.players.map((p) => [p.id, p]));
    for (const p of b.players) {
      const sp = this.players.get(p.id);
      if (!sp) continue;
      if (p.id === store.playerId) {
        sp.update(p, this.disp.x, this.disp.y, this.facing, store.defs);
        continue;
      }
      const q = pa.get(p.id) ?? p;
      const jump = Math.hypot(p.x - q.x, p.y - q.y) > 3;
      const x = jump ? p.x : q.x + (p.x - q.x) * t;
      const y = jump ? p.y : q.y + (p.y - q.y) * t;
      sp.update(p, x, y, p.facing, store.defs);
    }
    const ea = new Map(a.entities.map((e) => [e.id, e]));
    const eb = b === s ? null : new Map(b.entities.map((e) => [e.id, e]));
    for (const e of s.entities) {
      const sp = this.entities.get(e.id);
      if (!sp) continue;
      const cur = eb?.get(e.id) ?? e;
      const prev = ea.get(e.id) ?? cur;
      const jump = Math.hypot(cur.x - prev.x, cur.y - prev.y) > 3;
      sp.update(e, jump ? cur.x : prev.x + (cur.x - prev.x) * t, jump ? cur.y : prev.y + (cur.y - prev.y) * t, store.defs);
    }

    this.follow.setPosition(this.disp.x * TILE, this.disp.y * TILE);
    this.updateInteractHint();
    this.updateBubbles();
  }

  private interpFrames(): { a: Snapshot; b: Snapshot; t: number } {
    const snaps = store.snaps;
    const latest = snaps[snaps.length - 1].snap;
    const at = performance.now() - INTERP_MS;
    for (let i = snaps.length - 2; i >= 0; i--) {
      if (snaps[i].at <= at) {
        const n = snaps[i + 1];
        const t = Math.min(1, Math.max(0, (at - snaps[i].at) / Math.max(1, n.at - snaps[i].at)));
        return { a: snaps[i].snap, b: n.snap, t };
      }
    }
    return { a: latest, b: latest, t: 1 };
  }

  private updateInteractHint() {
    const s = store.latest;
    const defs = store.defs;
    let best: SnapEntity | null = null;
    let bd = Infinity;
    if (s && defs && store.phase?.phase === "play") {
      for (const e of s.entities) {
        const d = defs.entities[e.type];
        if (!d?.interactable) continue;
        const r = mergeVisual(d.visual, e.visual).size / 2;
        const dist = Math.hypot(e.x - this.disp.x, e.y - this.disp.y) - r - PLAYER_RADIUS;
        if (dist <= 1.5 && dist < bd) {
          best = e;
          bd = dist;
        }
      }
    }
    const id = best?.id ?? null;
    if (id !== this.interactTarget) {
      if (this.interactTarget) this.entities.get(this.interactTarget)?.hint.setVisible(false);
      if (id) this.entities.get(id)?.hint.setVisible(true);
      this.interactTarget = id;
    }
  }

  // ------------------------------------------------------------------ //
  private onKey(e: KeyboardEvent) {
    if (isTyping() || e.repeat) return;
    if (store.phase?.phase !== "play") return;
    if (e.code === "KeyE") {
      socket.send({ t: "interact" });
      return;
    }
    const m = /^Digit([1-9])$/.exec(e.code);
    if (m) {
      const i = Number(m[1]) - 1;
      if (e.shiftKey) {
        const items = Object.keys(store.defs?.items ?? {}).filter((k) => (store.me?.items[k] ?? 0) > 0);
        if (items[i]) socket.send({ t: "use_item", item: items[i] });
      } else {
        const ab = store.me?.abilities[i];
        if (ab) this.useAbility(ab.id);
      }
    }
  }

  private pointerTile(): { x: number; y: number } {
    const p = this.input.activePointer.positionToCamera(this.cameras.main) as Phaser.Math.Vector2;
    return { x: p.x / TILE, y: p.y / TILE };
  }

  private useAbility(id: string) {
    const ad = store.defs?.abilities.find((a) => a.id === id);
    const s = store.latest;
    if (!ad || !s) return;
    const ptr = this.pointerTile();
    const me = { x: this.disp.x, y: this.disp.y };
    switch (ad.targeting) {
      case "direction": {
        const dir = (Math.atan2(ptr.y - me.y, ptr.x - me.x) * 180) / Math.PI;
        socket.send({ t: "use_ability", ability: id, direction: dir });
        break;
      }
      case "point":
        socket.send({ t: "use_ability", ability: id, point: ptr });
        break;
      case "entity":
      case "player": {
        const cands: { id: string; x: number; y: number }[] = ad.targeting === "entity"
          ? s.entities
          : s.players.filter((p) => p.id !== store.playerId && !p.dead);
        const near = (o: { x: number; y: number }, c: { x: number; y: number }) => Math.hypot(o.x - c.x, o.y - c.y);
        let pick = cands.filter((c) => near(c, ptr) < 1.5).sort((a, b) => near(a, ptr) - near(b, ptr))[0];
        if (!pick) pick = cands.filter((c) => near(c, me) <= ad.range + 0.5).sort((a, b) => near(a, me) - near(b, me))[0];
        socket.send({ t: "use_ability", ability: id, target_id: pick?.id ?? null });
        break;
      }
      default:
        socket.send({ t: "use_ability", ability: id });
    }
  }

  // ------------------------------------------------------------------ //
  private anchorOf(id: string): { x: number; y: number; r: number } | null {
    if (id === store.playerId) return { x: this.disp.x * TILE, y: this.disp.y * TILE, r: this.players.get(id)?.radiusPx ?? 13 };
    const p = this.players.get(id);
    if (p) return { x: p.c.x, y: p.c.y, r: p.radiusPx };
    const e = this.entities.get(id);
    if (e) return { x: e.vis.outer.x, y: e.vis.outer.y, r: e.vis.radiusPx };
    return null;
  }

  private bubble(from: string, text: string) {
    this.bubbles = this.bubbles.filter((b) => {
      if (b.from === from) { b.text.destroy(); return false; }
      return true;
    });
    const t = this.add.text(0, 0, text.length > 80 ? text.slice(0, 77) + "…" : text, {
      fontFamily: TEXT_FONT, fontSize: "12px", color: "#1d1d1f", backgroundColor: "#ffffff",
      padding: { x: 6, y: 3 }, wordWrap: { width: 180 }, align: "center",
    }).setOrigin(0.5, 1).setDepth(DEPTH.labels);
    this.bubbles.push({ from, text: t, until: this.time.now + 4500 });
  }

  private updateBubbles() {
    const now = this.time.now;
    this.bubbles = this.bubbles.filter((b) => {
      const a = this.anchorOf(b.from);
      if (!a || now > b.until) {
        b.text.destroy();
        return false;
      }
      b.text.setPosition(a.x, a.y - a.r - 34);
      return true;
    });
  }

  private floatText(id: string, text: string, color: string) {
    const a = this.anchorOf(id);
    if (!a) return;
    const t = this.add.text(a.x, a.y - a.r - 10, text, {
      fontFamily: TEXT_FONT, fontSize: "16px", fontStyle: "bold", color, stroke: "#ffffff", strokeThickness: 4,
    }).setOrigin(0.5).setDepth(DEPTH.fx);
    this.tweens.add({ targets: t, y: t.y - 30, alpha: 0, duration: 1200, ease: "Cubic.easeOut", onComplete: () => t.destroy() });
  }

  private fx(m: Fx) {
    const x = m.x * TILE;
    const y = m.y * TILE;
    const color = hex(m.color, 0x222222);
    const tw = this.tweens;
    switch (m.kind) {
      case "burst":
        for (let i = 0; i < 12; i++) {
          const a = (i / 12) * Math.PI * 2;
          const c = this.add.circle(x, y, 4, color).setDepth(DEPTH.fx);
          tw.add({ targets: c, x: x + Math.cos(a) * 40, y: y + Math.sin(a) * 40, alpha: 0, scale: 0.4, duration: 500, ease: "Cubic.easeOut", onComplete: () => c.destroy() });
        }
        break;
      case "ring": {
        const g = this.add.circle(x, y, 8).setStrokeStyle(3, color).setDepth(DEPTH.fx);
        tw.add({ targets: g, scale: 6, alpha: 0, duration: 600, onComplete: () => g.destroy() });
        break;
      }
      case "flash": {
        const c = this.add.circle(x, y, 3 * TILE, m.color ? color : 0xffffff, 0.8).setDepth(DEPTH.fx);
        tw.add({ targets: c, alpha: 0, duration: 400, onComplete: () => c.destroy() });
        break;
      }
      case "shake": {
        const near = Math.hypot(m.x - this.disp.x, m.y - this.disp.y) < 8;
        if (near) this.cameras.main.shake(180, 0.004);
        break;
      }
      case "trail":
        for (let i = 0; i < 6; i++) {
          const c = this.add.circle(x + (Math.random() - 0.5) * 20, y + (Math.random() - 0.5) * 20, 3, color, 0.7).setDepth(DEPTH.fx);
          tw.add({ targets: c, alpha: 0, duration: 400 + i * 80, onComplete: () => c.destroy() });
        }
        break;
      case "confetti": {
        const colors = [0xe6194b, 0x3cb44b, 0x4363d8, 0xf58231, 0x911eb4, 0xe0a800];
        for (let i = 0; i < 24; i++) {
          const r = this.add.rectangle(x, y, 5, 8, colors[i % colors.length]).setDepth(DEPTH.fx);
          tw.add({
            targets: r, x: x + (Math.random() - 0.5) * 120, y: y + 30 + Math.random() * 50, angle: Math.random() * 720,
            alpha: 0, duration: 900 + Math.random() * 400, ease: "Quad.easeOut", onComplete: () => r.destroy(),
          });
        }
        break;
      }
      case "sound":
        beep();
        break;
    }
  }
}

let audio: AudioContext | null = null;
function beep() {
  try {
    audio ??= new AudioContext();
    const o = audio.createOscillator();
    const g = audio.createGain();
    o.frequency.value = 660;
    g.gain.setValueAtTime(0.06, audio.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.15);
    o.connect(g).connect(audio.destination);
    o.start();
    o.stop(audio.currentTime + 0.16);
  } catch { /* no audio */ }
}
