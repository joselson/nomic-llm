// Client-side prediction for the local player. Mirrors backend physics.step_players:
// each input command moves the player for INPUT_DT seconds; the server acks the last
// command it applied, and we replay the unacknowledged ones on top of its position.

export const INPUT_DT = 0.05;
export const PLAYER_RADIUS = 0.4;

export interface Collider { x: number; y: number; r: number; square: boolean }

interface Pending { seq: number; dx: number; dy: number }

function pushOutCircle(x: number, y: number, r: number, c: Collider): [number, number] {
  if (c.square) {
    const qx = Math.max(c.x - c.r, Math.min(x, c.x + c.r));
    const qy = Math.max(c.y - c.r, Math.min(y, c.y + c.r));
    const dx = x - qx;
    const dy = y - qy;
    const d = Math.hypot(dx, dy);
    if (d >= r) return [x, y];
    if (d > 1e-9) return [x + (dx / d) * (r - d), y + (dy / d) * (r - d)];
    const opts: [number, number][] = [
      [c.x - c.r - r, y], [c.x + c.r + r, y], [x, c.y - c.r - r], [x, c.y + c.r + r],
    ];
    opts.sort((a, b) => Math.hypot(a[0] - x, a[1] - y) - Math.hypot(b[0] - x, b[1] - y));
    return opts[0];
  }
  const dx = x - c.x;
  const dy = y - c.y;
  const d = Math.hypot(dx, dy);
  const overlap = r + c.r - d;
  if (overlap <= 0 || d < 1e-9) return [x, y];
  return [x + (dx / d) * overlap, y + (dy / d) * overlap];
}

export class Predictor {
  x = 0;
  y = 0;
  seq = 0;
  ready = false;
  private pending: Pending[] = [];

  reset(x: number, y: number) {
    this.x = x;
    this.y = y;
    this.pending = [];
    this.ready = true;
  }

  private move(dx: number, dy: number, speed: number, flags: string[], colliders: Collider[], map: number, size: number) {
    if (flags.includes("frozen")) return;
    if (flags.includes("reversed_controls")) {
      dx = -dx;
      dy = -dy;
    }
    const n = Math.hypot(dx, dy);
    if (n < 1e-3) return;
    if (n > 1) {
      dx /= n;
      dy /= n;
    }
    const r = PLAYER_RADIUS * Math.max(0.25, size);
    let x = this.x + dx * speed * INPUT_DT;
    let y = this.y + dy * speed * INPUT_DT;
    if (!flags.includes("ghost")) {
      for (let k = 0; k < 2; k++) {
        for (const c of colliders) {
          if (Math.abs(c.x - x) > c.r + r + 1 || Math.abs(c.y - y) > c.r + r + 1) continue;
          [x, y] = pushOutCircle(x, y, r, c);
        }
      }
    }
    this.x = Math.min(Math.max(x, r), map - r);
    this.y = Math.min(Math.max(y, r), map - r);
  }

  /** Apply a new local input command; returns its sequence number. */
  step(dx: number, dy: number, speed: number, flags: string[], colliders: Collider[], map: number, size: number): number {
    this.seq += 1;
    this.pending.push({ seq: this.seq, dx, dy });
    if (this.pending.length > 120) this.pending.shift();
    this.move(dx, dy, speed, flags, colliders, map, size);
    return this.seq;
  }

  reconcile(sx: number, sy: number, ack: number, speed: number, flags: string[], colliders: Collider[], map: number, size: number) {
    this.pending = this.pending.filter((p) => p.seq > ack);
    this.x = sx;
    this.y = sy;
    for (const p of this.pending) this.move(p.dx, p.dy, speed, flags, colliders, map, size);
  }
}
