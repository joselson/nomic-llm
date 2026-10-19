// Modal dialogs: idea box, prompts, patch notes, game over, help.
import { socket, type GameOver, type PatchNotes, type Prompt, type PromptClose } from "../net/socket";
import { bus, store } from "../state";
import { h, mount } from "./dom";

interface Modal { el: HTMLElement; close: () => void }

function openModal(content: HTMLElement, opts: { dismissable?: boolean; dark?: boolean; onClose?: () => void } = {}): Modal {
  const backdrop = h("div", { class: `backdrop${opts.dark ? " dark" : ""}` }, content);
  let closed = false;
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape" && opts.dismissable !== false) close();
  };
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener("keydown", onKey);
    rm();
    opts.onClose?.();
  };
  if (opts.dismissable !== false) {
    backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(); });
  }
  window.addEventListener("keydown", onKey);
  const rm = mount(backdrop);
  return { el: backdrop, close };
}

const STATUS_TEXT: Record<string, string> = {
  submitted: "Submitted ✓ — you can edit it until the play phase ends.",
  locked: "Locked in. The night will build it.",
  compiling: "Being built by the agents…",
  feasible: "Built ✓ — it's on the ballot.",
  infeasible: "Couldn't be built",
  timeout: "Ran out of time",
  won: "Won! 🎉",
  lost: "Didn't win this time",
  none: "",
};

// ---------------------------------------------------------------------------
function ideaBox(): Modal {
  const max = store.config?.idea_max_chars ?? 150;
  const ta = h("textarea", { maxLength: max, placeholder: "e.g. Mushrooms grow at night; eating one makes you tiny and fast for 10 s" });
  ta.value = store.idea?.text ?? "";
  const counter = h("div", { class: "idea-counter" });
  const status = h("div", { class: "small" });
  const submit = h("button", { class: "primary" }, store.idea?.text ? "Update idea" : "Submit idea");
  const upd = () => {
    counter.textContent = `${ta.value.length}/${max}`;
    const st = store.idea;
    status.textContent = st ? `${STATUS_TEXT[st.status] ?? st.status}${st.reason ? `: ${st.reason}` : ""}` : "";
    const open = store.phase?.phase === "play";
    ta.disabled = !open;
    submit.disabled = !open || !ta.value.trim();
  };
  ta.addEventListener("input", upd);
  ta.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit.click();
    }
  });
  const content = h("div", { class: "panel modal" },
    h("h2", null, "💡 Today's idea"),
    h("div", { class: "muted small" },
      "Describe one mechanic to add to the game. Tonight AI agents build every idea; players vote anonymously; the winner goes live tomorrow and its author gets +2 and a head start."),
    h("div", { style: "height:10px" }), ta, counter, status,
    h("div", { class: "actions" }, h("button", { onclick: () => m.close() }, "Close"), submit));
  const m = openModal(content);
  submit.onclick = () => {
    const text = ta.value.trim();
    if (text) socket.send({ t: "idea", text });
  };
  const off = bus.on("idea_status", () => {
    upd();
    if (store.idea?.status === "submitted") setTimeout(() => m.close(), 600);
  });
  const off2 = bus.on("phase", upd);
  const orig = m.close;
  m.close = () => { off(); off2(); orig(); };
  upd();
  setTimeout(() => ta.focus(), 0);
  return m;
}

// ---------------------------------------------------------------------------
function mechanicsList(): HTMLElement {
  const ms = store.defs?.mechanics ?? [];
  if (!ms.length) return h("div", { class: "muted small" }, "No mechanics yet — the world is blank.");
  return h("ul", { class: "mech-list" }, ...[...ms].reverse().map((m) => h("li", null,
    h("b", null, m.name), h("span", { class: "muted" }, ` · day ${m.day} · by ${m.creator_name}${m.disabled ? " · disabled" : ""}`),
    h("div", null, m.summary))));
}

function patchNotesView(n: PatchNotes, withYours = true): HTMLElement {
  const box = h("div", null, h("h3", null, `Day ${n.day}`));
  if (n.winner) {
    box.append(h("div", { class: "winner-card" },
      h("div", { class: "name" }, n.winner.name),
      h("div", { class: "muted small" }, `by ${n.winner.author}`),
      h("div", { style: "margin-top:6px" }, n.winner.summary)));
  } else {
    box.append(h("div", { class: "muted" }, "A quiet night — nothing new was deployed."));
  }
  if (n.also_proposed.length) box.append(h("div", { class: "small", style: "margin-top:8px" },
    h("span", { class: "muted" }, "Also proposed: "), n.also_proposed.join(" · ")));
  if (withYours && n.your_idea) {
    const ok = n.your_idea.status === "won";
    const bad = n.your_idea.status === "infeasible" || n.your_idea.status === "timeout";
    box.append(h("div", { class: "small", style: "margin-top:8px" }, "Your idea: ",
      h("span", { class: `badge ${ok ? "good" : bad ? "bad" : ""}` }, STATUS_TEXT[n.your_idea.status] ?? n.your_idea.status),
      n.your_idea.reason ? h("div", { class: "muted" }, n.your_idea.reason) : null));
  }
  for (const inc of n.incidents) box.append(h("div", { class: "small", style: "color:var(--bad)" }, `⚠️ ${inc}`));
  return box;
}

function patchNotesModal(n: PatchNotes): Modal {
  const content = h("div", { class: "panel modal" },
    h("h2", null, "📜 Patch notes"),
    patchNotesView(n),
    h("div", { class: "actions" }, h("button", { class: "primary", onclick: () => m.close() }, "Play")));
  const m = openModal(content);
  return m;
}

function historyModal(): Modal {
  const body = h("div");
  const hist = [...store.patchHistory].reverse();
  body.append(h("h3", null, "Active mechanics"), mechanicsList());
  if (hist.length) {
    body.append(h("h3", null, "History"));
    for (const n of hist) body.append(patchNotesView(n, false));
  }
  const content = h("div", { class: "panel modal" }, h("h2", null, "📜 Patch notes"), body,
    h("div", { class: "actions" }, h("button", { onclick: () => m.close() }, "Close")));
  const m = openModal(content);
  return m;
}

function helpModal(): Modal {
  const rows: [string, string][] = [
    ["WASD / arrows", "Move"], ["E", "Interact with the nearest thing"], ["1–9", "Use ability (aim with the mouse)"],
    ["Shift+1–9 / click", "Use item"], ["I", "Open the idea box"], ["Enter", "Chat"], ["Tab (hold)", "Scoreboard"],
  ];
  const content = h("div", { class: "panel modal" }, h("h2", null, "How to play"),
    h("p", { class: "small" },
      "Every day each player proposes one idea. At night AI agents turn the ideas into game mechanics, everyone votes anonymously, and the winner goes live the next day. Points only come from mechanics — so invent good ones. First to ",
      String(store.config?.win_points ?? 100), " points wins."),
    h("table", { class: "small" }, ...rows.map(([k, v]) => h("tr", null, h("td", null, h("span", { class: "kbd" }, k)), h("td", null, v)))),
    h("div", { class: "actions" }, h("button", { onclick: () => m.close() }, "Close")));
  const m = openModal(content);
  return m;
}

function gameOverModal(g: GameOver): Modal {
  const winners = g.standings.filter((s) => g.winner_ids.includes(s.id)).map((s) => s.name);
  const content = h("div", { class: "panel modal gameover" },
    h("h2", null, winners.length ? `🏆 ${winners.join(" & ")} win${winners.length > 1 ? "" : "s"}!` : "Game over"),
    h("h3", null, "Final standings"),
    h("table", null, ...g.standings.map((s, i) => h("tr", null,
      h("td", { class: "muted" }, String(i + 1)),
      h("td", null, h("span", { class: "dot", style: { background: s.color, display: "inline-block", verticalAlign: "middle", marginRight: "6px" } }), s.name),
      h("td", { style: "text-align:right;font-weight:700" }, String(s.points))))),
    h("h3", null, `The ${g.mechanics.length} mechanics of this game`),
    g.mechanics.length ? h("ul", { class: "mech-list" }, ...g.mechanics.map((m) => h("li", null,
      h("b", null, m.name), h("span", { class: "muted" }, ` · day ${m.day} · by ${m.author}`),
      m.summary ? h("div", null, m.summary) : null))) : h("div", { class: "muted" }, "None"),
    h("div", { class: "actions" }, h("button", { class: "primary", onclick: () => { location.href = location.pathname; } }, "New game")));
  return openModal(content, { dismissable: false });
}

// ---------------------------------------------------------------------------
function mountPrompts(): () => void {
  const queue: Prompt[] = [];
  let current: { p: Prompt; done: () => void } | null = null;
  const showNext = () => {
    if (current || !queue.length) return;
    const p = queue.shift()!;
    const line = h("div", { class: "timer-line" });
    const opts = h("div", { class: "prompt-options" });
    const content = h("div", { class: "panel modal" },
      h("div", { class: "muted small" }, p.vote ? "🗳️ Vote" : "❓ Choose"),
      h("h2", null, p.question), opts, line);
    const m = openModal(content, { dismissable: false });
    const timer = window.setTimeout(() => done(), p.timeout * 1000 + 500);
    const done = () => {
      if (current?.p !== p) return;
      window.clearTimeout(timer);
      m.close();
      current = null;
      showNext();
    };
    p.options.forEach((label, i) => {
      opts.append(h("button", { class: i === 0 ? "primary" : "", onclick: () => {
        socket.send({ t: "prompt_answer", prompt_id: p.prompt_id, option: i });
        if (p.vote) opts.querySelectorAll("button").forEach((b) => ((b as HTMLButtonElement).disabled = true));
        else done();
      } }, label));
    });
    line.animate([{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }], { duration: p.timeout * 1000, fill: "forwards" });
    current = { p, done };
  };
  const offs = [
    bus.on("prompt", (p: Prompt) => { queue.push(p); showNext(); }),
    bus.on("prompt_close", (c: PromptClose) => {
      const i = queue.findIndex((q) => q.prompt_id === c.prompt_id);
      if (i >= 0) queue.splice(i, 1);
      if (current?.p.prompt_id === c.prompt_id) current.done();
    }),
    bus.on("phase", () => {
      if (store.phase?.phase !== "play") {
        queue.length = 0;
        current?.done();
      }
    }),
  ];
  return () => {
    offs.forEach((f) => f());
    queue.length = 0;
    current?.done();
  };
}

export function mountModals(): () => void {
  let idea: Modal | null = null;
  let notes: Modal | null = null;
  let other: Modal | null = null;
  const closeAll = () => { idea?.close(); notes?.close(); other?.close(); };
  const offs = [
    bus.on("open:idea", () => {
      if (store.phase?.phase !== "play") return;
      idea?.close();
      idea = ideaBox();
    }),
    bus.on("open:notes", () => { closeAll(); notes = historyModal(); }),
    bus.on("open:help", () => { other?.close(); other = helpModal(); }),
    bus.on("patch_notes", (n: PatchNotes) => {
      if (store.phase?.phase === "game_over") return;
      notes?.close();
      notes = patchNotesModal(n);
    }),
    bus.on("game_over", (g: GameOver) => { closeAll(); other = gameOverModal(g); }),
    bus.on("phase", () => { if (store.phase?.phase !== "play") idea?.close(); }),
    mountPrompts(),
  ];
  if (store.gameOver) other = gameOverModal(store.gameOver);
  return () => {
    offs.forEach((f) => f());
    closeAll();
  };
}

