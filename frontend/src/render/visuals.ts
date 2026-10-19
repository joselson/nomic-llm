// DSL Visual → Phaser objects (SPEC §3.5).
import Phaser from "phaser";
import type { Visual } from "../net/socket";

export const TILE = 32;
export const DEPTH = { tiles: 0, zones: 1, ground: 2, object: 10, players: 20, overhead: 30, labels: 40, fx: 50 };
export const EMOJI_FONT = '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif';
export const TEXT_FONT = 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export const DEFAULT_VISUAL: Visual = {
  shape: "circle", color: "#222222", emoji: null, size: 1, label: null, opacity: 1, outline: null,
  glow: false, animation: "none", layer: "object",
};

export function mergeVisual(base: Partial<Visual> | null | undefined, over?: Partial<Visual> | null): Visual {
  const v = { ...DEFAULT_VISUAL, ...(base ?? {}) } as Visual;
  if (over) for (const [k, val] of Object.entries(over)) if (val !== null && val !== undefined) (v as any)[k] = val;
  return v;
}

export function hex(c: string | null | undefined, fallback = 0x222222): number {
  if (!c || !/^#[0-9a-fA-F]{6}$/.test(c)) return fallback;
  return parseInt(c.slice(1), 16);
}

export function shade(color: number, f: number): number {
  const r = Math.min(255, Math.round(((color >> 16) & 255) * f));
  const g = Math.min(255, Math.round(((color >> 8) & 255) * f));
  const b = Math.min(255, Math.round((color & 255) * f));
  return (r << 16) | (g << 8) | b;
}

function poly(n: number, r: number, rot = -Math.PI / 2, inner?: number): Phaser.Math.Vector2[] {
  const pts: Phaser.Math.Vector2[] = [];
  const steps = inner ? n * 2 : n;
  for (let i = 0; i < steps; i++) {
    const rr = inner && i % 2 ? inner : r;
    const a = rot + (i / steps) * Math.PI * 2;
    pts.push(new Phaser.Math.Vector2(Math.cos(a) * rr, Math.sin(a) * rr));
  }
  return pts;
}

/** Draw a shape centered at (0,0) with radius r (px). */
export function drawShape(
  g: Phaser.GameObjects.Graphics, shape: Visual["shape"], r: number, fill: number, alpha: number,
  outline: number | null, lineW = 2,
) {
  const stroke = outline ?? shade(fill, 0.6);
  g.fillStyle(fill, alpha);
  g.lineStyle(lineW, stroke, Math.min(1, alpha + 0.2));
  switch (shape) {
    case "circle":
      g.fillCircle(0, 0, r);
      g.strokeCircle(0, 0, r);
      break;
    case "square":
      g.fillRoundedRect(-r, -r, r * 2, r * 2, r * 0.18);
      g.strokeRoundedRect(-r, -r, r * 2, r * 2, r * 0.18);
      break;
    case "triangle": {
      const p = poly(3, r * 1.1);
      g.fillPoints(p, true);
      g.strokePoints(p, true);
      break;
    }
    case "diamond": {
      const p = poly(4, r);
      g.fillPoints(p, true);
      g.strokePoints(p, true);
      break;
    }
    case "hexagon": {
      const p = poly(6, r, 0);
      g.fillPoints(p, true);
      g.strokePoints(p, true);
      break;
    }
    case "star": {
      const p = poly(5, r * 1.1, -Math.PI / 2, r * 0.48);
      g.fillPoints(p, true);
      g.strokePoints(p, true);
      break;
    }
    case "ring":
      g.lineStyle(Math.max(3, r * 0.32), fill, alpha);
      g.strokeCircle(0, 0, r * 0.84);
      break;
    case "cross": {
      const w = r * 0.38;
      g.fillRect(-w, -r, w * 2, r * 2);
      g.fillRect(-r, -w, r * 2, w * 2);
      break;
    }
    case "none":
      break;
  }
}

/** A rendered DSL Visual: outer container (position/depth) > inner (animation) > glow, shape, emoji. */
export class VisualSprite {
  outer: Phaser.GameObjects.Container;
  private inner: Phaser.GameObjects.Container;
  private label: Phaser.GameObjects.Text | null = null;
  private tween: Phaser.Tweens.Tween | null = null;
  private key = "";
  radiusPx = TILE / 2;

  constructor(private scene: Phaser.Scene, v: Visual) {
    this.outer = scene.add.container(0, 0);
    this.inner = scene.add.container(0, 0);
    this.outer.add(this.inner);
    this.set(v);
  }

  set(v: Visual) {
    const key = JSON.stringify(v);
    if (key === this.key) return;
    this.key = key;
    this.tween?.remove();
    this.tween = null;
    this.inner.removeAll(true);
    this.inner.setScale(1).setAngle(0).setPosition(0, 0).setAlpha(1);
    this.label?.destroy();
    this.label = null;

    const r = (v.size * TILE) / 2;
    this.radiusPx = r;
    const color = hex(v.color);
    if (v.glow) {
      const glow = this.scene.add.graphics();
      glow.fillStyle(color, 0.12).fillCircle(0, 0, r * 1.9);
      glow.fillStyle(color, 0.18).fillCircle(0, 0, r * 1.45);
      this.inner.add(glow);
    }
    if (v.shape !== "none") {
      const g = this.scene.add.graphics();
      drawShape(g, v.shape, r, color, v.opacity, v.outline ? hex(v.outline) : null, Math.max(1.5, r * 0.09));
      this.inner.add(g);
    }
    if (v.emoji) {
      const fs = Math.max(12, v.shape === "none" ? r * 1.6 : r * 1.05);
      const t = this.scene.add.text(0, 0, v.emoji, { fontFamily: EMOJI_FONT, fontSize: `${fs}px` })
        .setOrigin(0.5).setAlpha(v.shape === "none" ? v.opacity : 1);
      this.inner.add(t);
    }
    if (v.label) {
      this.label = this.scene.add.text(0, r + 8, v.label, {
        fontFamily: TEXT_FONT, fontSize: "11px", color: "#555", stroke: "#ffffff", strokeThickness: 3,
      }).setOrigin(0.5, 0);
      this.outer.add(this.label);
    }
    const tw = this.scene.tweens;
    switch (v.animation) {
      case "pulse":
        this.tween = tw.add({ targets: this.inner, scale: 1.12, duration: 600, yoyo: true, repeat: -1, ease: "Sine.easeInOut" });
        break;
      case "spin":
        this.tween = tw.add({ targets: this.inner, angle: 360, duration: 3000, repeat: -1 });
        break;
      case "bob":
        this.tween = tw.add({ targets: this.inner, y: -4, duration: 700, yoyo: true, repeat: -1, ease: "Sine.easeInOut" });
        break;
      case "shake":
        this.tween = tw.add({ targets: this.inner, x: 1.5, duration: 50, yoyo: true, repeat: -1 });
        break;
      case "blink":
        this.tween = tw.add({ targets: this.inner, alpha: 0.25, duration: 500, yoyo: true, repeat: -1 });
        break;
    }
  }

  destroy() {
    this.tween?.remove();
    this.outer.destroy(true);
  }
}

export function layerDepth(layer: Visual["layer"]): number {
  return DEPTH[layer] ?? DEPTH.object;
}
