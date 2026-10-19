// Player and entity display objects.
import Phaser from "phaser";
import type { Defs, SnapEntity, SnapPlayer } from "../net/socket";
import {
  DEPTH, EMOJI_FONT, TEXT_FONT, TILE, VisualSprite, drawShape, hex, layerDepth, mergeVisual,
} from "./visuals";
import { PLAYER_RADIUS } from "./predict";

function statusEmojis(ids: string[] | null | undefined, defs: Defs | null): string {
  if (!ids?.length || !defs) return "";
  return ids.map((s) => defs.statuses[s]?.visual?.emoji ?? "•").join("");
}

function drawHp(g: Phaser.GameObjects.Graphics, hp: number | null, max: number | null, y: number, w: number) {
  g.clear();
  if (hp == null || max == null || max <= 0 || hp >= max) return;
  const f = Math.max(0, Math.min(1, hp / max));
  g.fillStyle(0xdddddd, 1).fillRect(-w / 2, y, w, 4);
  g.fillStyle(f > 0.5 ? 0x2e9e5b : f > 0.25 ? 0xe0a800 : 0xd64545, 1).fillRect(-w / 2, y, w * f, 4);
}

export class PlayerSprite {
  c: Phaser.GameObjects.Container;
  private body: Phaser.GameObjects.Graphics;
  private eyes: Phaser.GameObjects.Graphics;
  private marker: Phaser.GameObjects.Graphics;
  private emoji: Phaser.GameObjects.Text;
  private nameText: Phaser.GameObjects.Text;
  private badges: Phaser.GameObjects.Text;
  private hp: Phaser.GameObjects.Graphics;
  private key = "";
  private r = PLAYER_RADIUS * TILE;

  constructor(scene: Phaser.Scene, readonly id: string, readonly isMe: boolean) {
    this.c = scene.add.container(0, 0).setDepth(DEPTH.players);
    this.marker = scene.add.graphics();
    this.body = scene.add.graphics();
    this.eyes = scene.add.graphics();
    this.emoji = scene.add.text(0, 0, "", { fontFamily: EMOJI_FONT, fontSize: "16px" }).setOrigin(0.5);
    this.nameText = scene.add.text(0, 0, "", {
      fontFamily: TEXT_FONT, fontSize: "12px", color: "#222", stroke: "#ffffff", strokeThickness: 3,
      fontStyle: isMe ? "bold" : "normal",
    }).setOrigin(0.5, 1);
    this.badges = scene.add.text(0, 0, "", { fontFamily: EMOJI_FONT, fontSize: "14px" }).setOrigin(0.5, 1);
    this.hp = scene.add.graphics();
    this.c.add([this.marker, this.body, this.eyes, this.emoji, this.hp, this.nameText, this.badges]);
  }

  update(p: SnapPlayer, x: number, y: number, facing: number, defs: Defs | null) {
    this.c.setPosition(x * TILE, y * TILE);
    const v = p.visual ?? {};
    const size = p.size * (v.size ? v.size : 1);
    const r = PLAYER_RADIUS * TILE * size;
    const color = hex(v.color ?? p.color);
    const shape = v.shape && v.shape !== "none" ? v.shape : "circle";
    const key = `${color}|${r}|${shape}|${p.flags.join(",")}`;
    if (key !== this.key) {
      this.key = key;
      this.r = r;
      this.body.clear();
      drawShape(this.body, shape, r, color, 1, p.flags.includes("frozen") ? 0x5ab4f0 : 0x222222, 2);
      this.marker.clear();
      if (p.flags.includes("marked")) this.marker.lineStyle(3, 0xd64545, 0.9).strokeCircle(0, 0, r + 6);
    }
    // eyes look where we're heading
    const a = (facing * Math.PI) / 180;
    this.eyes.clear();
    if (!v.emoji) {
      this.eyes.fillStyle(0x111111, 1);
      for (const off of [-0.6, 0.6]) {
        this.eyes.fillCircle(Math.cos(a + off) * r * 0.48, Math.sin(a + off) * r * 0.48, Math.max(1.8, r * 0.15));
      }
    }
    this.emoji.setText(v.emoji ?? "").setFontSize(r * 1.3);
    this.nameText.setText(p.connected ? p.name : `${p.name} (away)`).setY(-r - 4);
    const badge = statusEmojis(p.statuses, defs) + (p.dead ? "💀" : "");
    this.badges.setText(badge).setY(-r - 20);
    drawHp(this.hp, p.hp, p.max_hp, r + 5, Math.max(24, r * 2));
    let alpha = v.opacity ?? 1;
    if (!p.connected) alpha *= 0.35;
    if (p.dead) alpha *= 0.2;
    if (p.flags.includes("invisible") || p.flags.includes("hidden")) alpha *= 0.45;
    if (p.flags.includes("ghost")) alpha *= 0.6;
    this.c.setAlpha(alpha);
    this.c.setDepth(DEPTH.players + y / 1000);
  }

  get radiusPx() {
    return this.r;
  }

  destroy() {
    this.c.destroy(true);
  }
}

export class EntitySprite {
  vis: VisualSprite;
  private props: Phaser.GameObjects.Text;
  private badges: Phaser.GameObjects.Text;
  private hp: Phaser.GameObjects.Graphics;
  hint: Phaser.GameObjects.Text;

  constructor(scene: Phaser.Scene, readonly id: string, e: SnapEntity, defs: Defs | null) {
    const def = defs?.entities[e.type];
    this.vis = new VisualSprite(scene, mergeVisual(def?.visual, e.visual));
    this.props = scene.add.text(0, 0, "", {
      fontFamily: EMOJI_FONT, fontSize: "11px", color: "#333", stroke: "#ffffff", strokeThickness: 3,
    }).setOrigin(0.5, 0);
    this.badges = scene.add.text(0, 0, "", { fontFamily: EMOJI_FONT, fontSize: "12px" }).setOrigin(0.5, 1);
    this.hp = scene.add.graphics();
    this.hint = scene.add.text(0, 0, "E", {
      fontFamily: TEXT_FONT, fontSize: "11px", color: "#fff", backgroundColor: "#222", padding: { x: 4, y: 1 },
    }).setOrigin(0.5, 1).setVisible(false);
    this.vis.outer.add([this.props, this.badges, this.hp, this.hint]);
  }

  update(e: SnapEntity, x: number, y: number, defs: Defs | null) {
    const def = defs?.entities[e.type];
    const v = mergeVisual(def?.visual, e.visual);
    this.vis.set(v);
    const r = this.vis.radiusPx;
    this.vis.outer.setPosition(x * TILE, y * TILE).setDepth(layerDepth(v.layer) + y / 1000);
    let txt = "";
    if (e.props && def) {
      txt = def.props
        .filter((p) => e.props![p.name] !== undefined && e.props![p.name] !== null)
        .map((p) => `${p.emoji ?? p.label ?? p.name}${p.emoji ? "" : ":"}${String(e.props![p.name])}`)
        .join(" ");
    }
    this.props.setText(txt).setY(r + (v.label ? 20 : 3));
    this.badges.setText(statusEmojis(e.statuses, defs)).setY(-r - 2);
    this.hint.setY(-r - (e.statuses?.length ? 16 : 3));
    drawHp(this.hp, e.hp, e.max_hp, -r - 8, Math.max(20, r * 1.6));
  }

  destroy() {
    this.vis.destroy();
  }
}
