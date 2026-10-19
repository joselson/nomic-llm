// Dev tools (server started with DEBUG=1).
import { bus, store } from "../state";
import { h, mount } from "./dom";

async function post(path: string, body: unknown = {}) {
  const res = await fetch(`/api/rooms/${store.code}/debug/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) bus.emit("toast", `debug ${path}: ${JSON.stringify(data.errors ?? data.detail ?? data)}`);
  return data;
}

export function mountDebug(): () => void {
  if (!store.config?.debug) return () => {};
  const sel = h("select");
  fetch("/api/debug/examples").then((r) => r.json()).then((d) => {
    for (const ex of d.examples ?? []) sel.append(h("option", { value: ex }, ex));
  }).catch(() => {});
  const file = h("input", { type: "file", accept: ".json", style: "display:none" });
  file.onchange = async () => {
    const f = file.files?.[0];
    if (!f) return;
    try {
      await post("load", { mechanic: JSON.parse(await f.text()) });
    } catch (e) {
      bus.emit("toast", `Bad JSON: ${(e as Error).message}`);
    }
    file.value = "";
  };
  const box = h("div", { class: "panel debugbar" },
    h("b", null, "debug"),
    h("div", { class: "row" }, sel, h("button", { onclick: () => post("load", { example: sel.value }) }, "Load")),
    h("button", { onclick: () => file.click() }, "Load JSON…"), file,
    h("button", { onclick: () => post("skip") }, "Skip phase"),
    h("button", { onclick: () => post("ff", { seconds: 60 }) }, "FF 60 s"),
    h("button", { onclick: () => post("points", { player_id: store.playerId, amount: 10 }) }, "+10 points"),
  );
  return mount(box);
}
