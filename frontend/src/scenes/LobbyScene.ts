import Phaser from "phaser";
import { socket } from "../net/socket";
import { bus, store } from "../state";
import { clear, h, mount } from "../ui/dom";

const SETTINGS: [string, string][] = [
  ["day0_seconds", "Day 0 length (s)"],
  ["play_seconds", "Play phase (s)"],
  ["night_seconds", "Night (s)"],
  ["max_days", "Max days"],
  ["win_points", "Points to win"],
];

export class LobbyScene extends Phaser.Scene {
  constructor() {
    super("lobby");
  }

  create() {
    if (store.phase && store.phase.phase !== "lobby") {
      this.scene.start("world");
      return;
    }
    const list = h("ul", { class: "lobby-players" });
    const settingsBox = h("div", { class: "settings" });
    const inputs = new Map<string, HTMLInputElement>();
    for (const [key, label] of SETTINGS) {
      const inp = h("input", { type: "number", min: "1", step: "1" });
      inputs.set(key, inp);
      settingsBox.append(h("label", null, label), inp);
    }
    const startBtn = h("button", { class: "primary" }, "Start game");
    const waiting = h("div", { class: "muted small" }, "Waiting for the host to start…");
    const hostArea = h("div", { class: "col" }, h("b", null, "Settings"), settingsBox, startBtn);
    const link = `${location.origin}${location.pathname}?room=${store.code}`;
    const copyBtn = h("button", { class: "small" }, "Copy invite link");
    copyBtn.onclick = async () => {
      try {
        await navigator.clipboard.writeText(link);
        copyBtn.textContent = "Copied ✓";
      } catch {
        copyBtn.textContent = link;
      }
    };

    startBtn.onclick = () => {
      const settings: Record<string, number> = {};
      for (const [k, inp] of inputs) {
        const v = Number(inp.value);
        if (Number.isFinite(v) && v > 0) settings[k] = v;
      }
      socket.send({ t: "start", settings });
    };

    let filled = false;
    const render = () => {
      const l = store.lobby;
      clear(list);
      for (const p of l?.players ?? []) {
        list.append(h("li", null,
          h("span", { class: "dot", style: { background: p.color } }),
          h("span", { class: "grow" }, p.name, p.id === store.playerId ? h("span", { class: "muted" }, " (you)") : null),
          p.is_host ? h("span", { title: "Host" }, "👑") : null,
          !p.connected ? h("span", { class: "muted small" }, "away") : null,
        ));
      }
      if (l && !filled) {
        for (const [k, inp] of inputs) inp.value = String(l.settings[k] ?? "");
        filled = true;
      }
      hostArea.style.display = store.isHost ? "" : "none";
      waiting.style.display = store.isHost ? "none" : "";
      startBtn.disabled = (l?.players.length ?? 0) < 1;
    };

    const card = h("div", { class: "panel center-card col" },
      h("div", { class: "muted small" }, "Room code"),
      h("div", { class: "row" }, h("div", { class: "code-big grow" }, store.code), copyBtn),
      h("div", { class: "small muted" },
        "Each day everyone proposes one idea. AI agents turn the ideas into mechanics, you vote, and the winner goes live tomorrow."),
      list,
      hostArea,
      waiting,
    );
    const unmount = mount(card);
    const offs = [
      bus.on("lobby", render),
      bus.on("phase", () => {
        if (store.phase && store.phase.phase !== "lobby") this.scene.start("world");
      }),
    ];
    this.events.once("shutdown", () => {
      unmount();
      offs.forEach((f) => f());
    });
    render();
  }
}
