// Inventory bar, ability bar, own status chips.
import { bus, store } from "../state";
import { clear, h, mount } from "./dom";

interface Slot { el: HTMLDivElement; sweep: HTMLDivElement; cd: HTMLSpanElement; count: HTMLSpanElement }

function costText(cost: { what: string; amount: number }[]): string {
  return cost.map((c) => {
    const item = store.defs?.items[c.what];
    return `${c.amount}${item?.visual.emoji ?? (c.what === "points" ? "⭐" : " " + c.what)}`;
  }).join(" ");
}

export function mountBars(): () => void {
  const statuses = h("div", { class: "statusbar" });
  const abilities = h("div", { class: "panel bar" });
  const items = h("div", { class: "panel bar" });
  const stack = h("div", { class: "bottom-stack" }, statuses, abilities, items);
  const remove = mount(stack);
  const aSlots = new Map<string, Slot>();
  const iSlots = new Map<string, Slot>();
  let aKey = "";
  let iKey = "";

  const slot = (icon: string, key: string, tip: HTMLElement, onClick: () => void): Slot => {
    const sweep = h("div", { class: "sweep" });
    const cd = h("span", { class: "cd" });
    const count = h("span", { class: "count" });
    const el = h("div", { class: "slot" },
      h("span", { class: "key" }, key), h("span", null, icon), sweep, cd, count, h("div", { class: "panel tooltip" }, tip));
    el.onclick = onClick;
    return { el, sweep, cd, count };
  };

  const render = () => {
    const me = store.me;
    const defs = store.defs;
    if (!me || !defs) return;
    // abilities (rebuild only when the set changes)
    const ak = me.abilities.map((a) => a.id).join(",");
    if (ak !== aKey) {
      aKey = ak;
      clear(abilities);
      aSlots.clear();
      me.abilities.forEach((a, i) => {
        const d = defs.abilities.find((x) => x.id === a.id);
        if (!d) return;
        const tip = h("div", null, h("b", null, d.name), h("div", null, d.description),
          d.cost.length ? h("div", { class: "muted" }, `Cost: ${costText(d.cost)}`) : null,
          d.targeting !== "none" && d.targeting !== "self" ? h("div", { class: "muted" }, `Aim with the mouse (${d.targeting})`) : null);
        const s = slot(d.icon ?? "✨", String(i + 1), tip, () => bus.emit("action:ability", a.id));
        if (d.cost.length) s.el.append(h("span", { class: "cost" }, costText(d.cost)));
        aSlots.set(a.id, s);
        abilities.append(s.el);
      });
    }
    abilities.style.display = me.abilities.length ? "" : "none";
    for (const a of me.abilities) {
      const s = aSlots.get(a.id);
      if (!s) continue;
      const frac = a.cooldown_total > 0 ? Math.min(1, a.cooldown / a.cooldown_total) : 0;
      s.sweep.style.background = frac > 0 ? `conic-gradient(rgba(0,0,0,.45) ${frac * 360}deg, transparent 0)` : "";
      s.cd.textContent = a.cooldown > 0 ? String(Math.ceil(a.cooldown)) : "";
      const uses = a.uses ?? a.left_today;
      s.count.textContent = uses != null ? `×${uses}` : "";
      s.el.classList.toggle("unusable", a.left_today === 0);
    }
    // items
    const owned = Object.keys(defs.items).filter((k) => (me.items[k] ?? 0) > 0);
    const ik = owned.join(",");
    if (ik !== iKey) {
      iKey = ik;
      clear(items);
      iSlots.clear();
      owned.forEach((id, i) => {
        const d = defs.items[id];
        const tip = h("div", null, h("b", null, d.name), d.usable ? h("div", { class: "muted" }, `Click or Shift+${i + 1} to use`) : null);
        const s = slot(d.visual.emoji ?? "■", d.usable ? `⇧${i + 1}` : "", tip, () => bus.emit("action:item", id));
        if (!d.visual.emoji) s.el.style.color = d.visual.color;
        iSlots.set(id, s);
        items.append(s.el);
      });
    }
    items.style.display = owned.length ? "" : "none";
    for (const id of owned) {
      const s = iSlots.get(id);
      if (s) s.count.textContent = String(me.items[id]);
    }
    // statuses + visible props
    clear(statuses);
    for (const st of me.statuses) {
      const d = defs.statuses[st.id];
      statuses.append(h("span", { class: "status-chip" },
        `${d?.visual?.emoji ?? "•"} ${d?.name ?? st.id}${st.stacks > 1 ? ` ×${st.stacks}` : ""} · ${Math.ceil(st.left)}s`));
    }
    for (const [k, v] of Object.entries(me.props)) {
      const d = defs.props[k];
      if (!d) continue;
      statuses.append(h("span", { class: "status-chip" }, `${d.emoji ?? ""} ${d.label}: ${String(v)}`));
    }
  };
  const offs = [bus.on("snapshot", render), bus.on("defs", () => { aKey = ""; iKey = ""; render(); })];
  render();
  return () => {
    offs.forEach((f) => f());
    remove();
  };
}
