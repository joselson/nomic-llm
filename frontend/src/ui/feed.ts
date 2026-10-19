// Toasts, announcements, the log/chat feed and the chat box.
import { socket, type ErrorMsg, type Message, type Points } from "../net/socket";
import { bus, playerName, store } from "../state";
import { h, isTyping, mount } from "./dom";

export function mountFeed(): () => void {
  const toasts = h("div", { class: "toasts" });
  const feed = h("div", { class: "feed" });
  const chatInput = h("input", { placeholder: "Say something… (Enter to send, Esc to cancel)", maxLength: 200 });
  const chat = h("div", { class: "chatbox", style: { display: "none" } }, chatInput);
  let announceEl: HTMLElement | null = null;
  const removers = [mount(toasts), mount(feed), mount(chat)];

  const toast = (text: string, cls = "") => {
    const t = h("div", { class: `panel toast ${cls}` }, text);
    toasts.append(t);
    while (toasts.children.length > 4) toasts.firstChild!.remove();
    setTimeout(() => t.remove(), 3500);
  };
  const announce = (text: string) => {
    announceEl?.remove();
    const el = h("div", { class: "announce" }, text);
    announceEl = el;
    const rm = mount(el);
    setTimeout(() => { rm(); if (announceEl === el) announceEl = null; }, 3200);
  };
  const line = (...parts: (string | HTMLElement)[]) => {
    const l = h("div", { class: "line" }, ...parts);
    feed.append(l);
    while (feed.children.length > 8) feed.firstChild!.remove();
    setTimeout(() => { l.style.opacity = "0"; }, 12000);
    setTimeout(() => l.remove(), 12600);
  };

  const onMessage = (m: Message) => {
    switch (m.style) {
      case "toast": toast(m.text); break;
      case "announce": announce(m.text); break;
      case "log": case "system": line(m.text); break;
      case "chat": line(h("b", null, `${m.name ?? playerName(m.from)}: `), m.text); break;
      case "bubble": line(h("b", null, `${playerName(m.from) || "…"} `), `“${m.text}”`); break;
    }
  };
  const onPoints = (m: Points) => {
    const who = m.player_id === store.playerId ? "You" : playerName(m.player_id);
    const sign = m.delta > 0 ? "+" : "";
    if (m.player_id === store.playerId) toast(`${sign}${m.delta} ⭐ ${m.reason ?? ""}`, m.delta > 0 ? "good" : "bad");
    else line(`${who} ${sign}${m.delta}${m.reason ? ` · ${m.reason}` : ""}`);
  };
  const onError = (m: ErrorMsg) => toast(m.message, "bad");

  const openChat = () => {
    chat.style.display = "";
    feed.style.bottom = "56px";
    chatInput.focus();
  };
  const closeChat = () => {
    chatInput.value = "";
    chat.style.display = "none";
    feed.style.bottom = "";
    chatInput.blur();
  };
  chatInput.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") {
      const text = chatInput.value.trim();
      if (text) socket.send({ t: "chat", text });
      closeChat();
    } else if (e.key === "Escape") closeChat();
  });
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !isTyping()) {
      e.preventDefault();
      openChat();
    }
  };
  window.addEventListener("keydown", onKey);
  const offs = [bus.on("message", onMessage), bus.on("points", onPoints), bus.on("error", onError),
    bus.on("toast", (t: string) => toast(t))];
  return () => {
    window.removeEventListener("keydown", onKey);
    offs.forEach((f) => f());
    removers.forEach((f) => f());
    announceEl?.remove();
  };
}
