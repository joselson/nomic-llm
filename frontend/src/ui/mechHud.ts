// HUD elements declared by mechanics (Mechanic.hud), placed at their anchors.
import type { Anchor, HudValue } from "../net/socket";
import { bus, fmtTime, store } from "../state";
import { clear, h, mount } from "./dom";

const ANCHORS: Anchor[] = ["top_left", "top", "top_right", "bottom_left", "bottom", "bottom_right"];

function show(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return String(Math.round(v * 100) / 100);
  return String(v);
}

function render(hv: HudValue): HTMLElement {
  const el = h("div", { class: "panel el" });
  switch (hv.kind) {
    case "bar": {
      const max = Number(hv.max) || 1;
      const f = Math.max(0, Math.min(1, Number(hv.value) / max));
      el.append(h("div", null, `${hv.label} `, h("b", null, `${show(hv.value)}/${show(hv.max)}`)),
        h("div", { class: "meter" }, h("div", { style: { width: `${f * 100}%` } })));
      break;
    }
    case "timer":
      el.append(`${hv.label} `, h("b", null, fmtTime(Number(hv.value) || 0)));
      break;
    case "leaderboard": {
      el.append(h("div", null, h("b", null, hv.label)));
      const rows = Array.isArray(hv.value) ? (hv.value as { name: string; value: unknown }[]) : [];
      for (const r of rows) el.append(h("div", { class: "row small" }, h("span", { class: "grow" }, r.name), h("b", null, show(r.value))));
      break;
    }
    case "icon":
      el.append(`${show(hv.value)} `, h("span", { class: "muted small" }, hv.label));
      break;
    case "text":
      el.append(h("span", { class: "muted small" }, `${hv.label} `), show(hv.value));
      break;
    default:
      el.append(`${hv.label}: `, h("b", null, show(hv.value)));
  }
  return el;
}

export function mountMechHud(): () => void {
  const boxes = new Map<Anchor, HTMLDivElement>();
  const removers: (() => void)[] = [];
  for (const a of ANCHORS) {
    const box = h("div", { class: `mhud ${a} passthrough` });
    boxes.set(a, box);
    removers.push(mount(box));
  }
  let lastKey = "";
  const update = () => {
    const key = JSON.stringify(store.hud);
    if (key === lastKey) return;
    lastKey = key;
    for (const b of boxes.values()) clear(b);
    for (const hv of store.hud) boxes.get(hv.anchor)?.append(render(hv));
  };
  const off = bus.on("snapshot", update);
  return () => {
    off();
    removers.forEach((f) => f());
  };
}
