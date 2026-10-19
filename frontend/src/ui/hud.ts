// Top bar, corner buttons, Tab scoreboard, connection status.
import { socket } from "../net/socket";
import { bus, fmtTime, secondsLeft, store } from "../state";
import { clear, h, isTyping, mount } from "./dom";

export function phaseLabel(): string {
  const p = store.phase;
  if (!p) return "";
  if (p.phase === "lobby") return "Lobby";
  if (p.phase === "game_over") return "Game over";
  if (p.phase === "night") {
    const st = p.stage === "vote" ? "voting" : p.stage === "results" ? "results" : "building ideas";
    return `Night ${p.day} · ${st}`;
  }
  return p.day === 0 ? "Day 0 · setup" : `Day ${p.day} · play`;
}

export function mountHud(): () => void {
  const phase = h("span", { class: "phase" });
  const clock = h("span", { class: "clock" });
  const mine = h("span");
  const leader = h("span", { class: "muted" });
  const fill = h("div");
  const mark = h("i");
  const bar = h("div", { class: "progress", title: "Progress to the win" }, fill, mark);
  const top = h("div", { class: "panel topbar" }, phase, clock, mine, bar, leader);

  const ideaBtn = h("button", { class: "idea-btn", title: "Submit today's idea (I)" }, "💡 Idea");
  ideaBtn.onclick = () => bus.emit("open:idea");
  const notesBtn = h("button", { title: "Patch notes & active mechanics" }, "📜 Patch notes");
  notesBtn.onclick = () => bus.emit("open:notes");
  const helpBtn = h("button", { title: "Controls" }, "?");
  helpBtn.onclick = () => bus.emit("open:help");
  const corner = h("div", { class: "corner-buttons" }, ideaBtn, notesBtn, helpBtn);

  const board = h("div", { class: "panel scoreboard", style: { display: "none" } });
  const status = h("div", { class: "panel conn-status", style: { display: "none" } });

  const removers = [mount(top), mount(corner), mount(board), mount(status)];

  const renderPoints = () => {
    const s = store.latest;
    const win = store.config?.win_points ?? 100;
    const me = s?.players.find((p) => p.id === store.playerId);
    const lead = s?.players.reduce((a, b) => (b.points > (a?.points ?? -1) ? b : a), undefined as typeof me);
    mine.textContent = `⭐ ${me?.points ?? 0}`;
    leader.textContent = lead && lead.id !== me?.id ? `Leader: ${lead.name} ${lead.points}` : lead ? "You lead" : "";
    fill.style.width = `${Math.min(100, ((me?.points ?? 0) / win) * 100)}%`;
    fill.style.background = me?.color ?? "#222";
    mark.style.left = `${Math.min(99, ((lead?.points ?? 0) / win) * 100)}%`;
  };
  const renderBoard = () => {
    const s = store.latest;
    clear(board);
    const rows = [...(s?.players ?? [])].sort((a, b) => b.points - a.points);
    board.append(h("table", null, ...rows.map((p, i) => h("tr", null,
      h("td", { class: "muted" }, `${i + 1}`),
      h("td", null, h("span", { class: "dot", style: { background: p.color, display: "inline-block", verticalAlign: "middle", marginRight: "6px" } }), p.name),
      h("td", { class: "pts" }, String(p.points)),
    ))));
  };
  const renderIdeaBtn = () => {
    const st = store.idea?.status;
    const done = st === "submitted" || st === "locked" || st === "compiling" || st === "feasible";
    ideaBtn.classList.toggle("done", done);
    ideaBtn.textContent = done ? "💡 Idea ✓" : "💡 Idea";
    ideaBtn.disabled = store.phase?.phase !== "play";
  };
  const tickClock = () => {
    phase.textContent = phaseLabel();
    clock.textContent = store.phase?.ends_at ? fmtTime(secondsLeft()) : "";
  };
  const iv = window.setInterval(tickClock, 250);
  tickClock();
  renderPoints();
  renderIdeaBtn();

  const onKey = (e: KeyboardEvent) => {
    if (e.code === "Tab") {
      e.preventDefault();
      if (e.type === "keydown") {
        renderBoard();
        board.style.display = "";
      } else board.style.display = "none";
      return;
    }
    if (e.type === "keydown" && !isTyping() && !e.repeat && e.code === "KeyI" && store.phase?.phase === "play") {
      e.preventDefault();
      bus.emit("open:idea");
    }
  };
  window.addEventListener("keydown", onKey);
  window.addEventListener("keyup", onKey);
  socket.onStatus = (st) => {
    status.style.display = st === "open" ? "none" : "";
    status.textContent = st === "reconnecting" ? "Connection lost — reconnecting…" : "Disconnected. Reload to rejoin.";
  };

  const offs = [
    bus.on("snapshot", () => {
      renderPoints();
      if (board.style.display !== "none") renderBoard();
    }),
    bus.on("phase", () => { tickClock(); renderIdeaBtn(); }),
    bus.on("idea_status", renderIdeaBtn),
  ];
  return () => {
    window.clearInterval(iv);
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("keyup", onKey);
    offs.forEach((f) => f());
    removers.forEach((f) => f());
  };
}
