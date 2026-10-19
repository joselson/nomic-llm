import Phaser from "phaser";
import { socket } from "../net/socket";
import { h, mount } from "../ui/dom";

const NAME_KEY = "pn_name";

function roomFromUrl(): string {
  const q = new URLSearchParams(location.search).get("room");
  return (q || location.hash.replace(/^#/, "")).toUpperCase().replace(/[^A-Z]/g, "").slice(0, 6);
}

export class JoinScene extends Phaser.Scene {
  constructor() {
    super("join");
  }

  create() {
    let saved = "";
    try { saved = localStorage.getItem(NAME_KEY) ?? ""; } catch { /* ignore */ }
    const name = h("input", { placeholder: "Nickname", maxLength: 16, value: saved, autocomplete: "off" });
    const code = h("input", {
      placeholder: "Room code", maxLength: 6, value: roomFromUrl(), autocomplete: "off",
      style: "text-transform: uppercase; letter-spacing: 3px; font-family: ui-monospace, monospace",
    });
    const err = h("div", { class: "error-text" });
    const joinBtn = h("button", { class: "primary grow" }, "Join room");
    const createBtn = h("button", { class: "grow" }, "Create room");

    const busy = (b: boolean) => {
      joinBtn.disabled = b;
      createBtn.disabled = b;
    };

    const join = async (roomCode: string) => {
      const nick = name.value.trim();
      if (!nick) {
        err.textContent = "Pick a nickname first.";
        name.focus();
        return;
      }
      roomCode = roomCode.trim().toUpperCase();
      if (roomCode.length !== 6) {
        err.textContent = "Room codes have 6 letters.";
        code.focus();
        return;
      }
      try { localStorage.setItem(NAME_KEY, nick); } catch { /* ignore */ }
      busy(true);
      err.textContent = "";
      try {
        await socket.connect(roomCode, nick);
        history.replaceState(null, "", `?room=${roomCode}`);
        this.scene.start("lobby");
      } catch (e) {
        err.textContent = (e as Error).message || "Could not join.";
        busy(false);
      }
    };

    joinBtn.onclick = () => join(code.value);
    createBtn.onclick = async () => {
      busy(true);
      try {
        const res = await fetch("/api/rooms", { method: "POST" });
        const { code: c } = await res.json();
        code.value = c;
        await join(c);
      } catch {
        err.textContent = "Server unreachable.";
        busy(false);
      }
    };
    for (const inp of [name, code]) {
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") join(code.value);
      });
    }

    const card = h("div", { class: "panel center-card col" },
      h("h1", { class: "title" }, "PATCH NOTES"),
      h("div", { class: "tagline" }, "Invent the game while you play it. First to 100 points wins."),
      name,
      code,
      err,
      h("div", { class: "row" }, joinBtn, createBtn),
    );
    const unmount = mount(card);
    this.events.once("shutdown", unmount);
    (saved ? code : name).focus();
  }
}
