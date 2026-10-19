// Night overlay: build progress → anonymous vote → results (SPEC §3.4).
import { socket } from "../net/socket";
import { bus, fmtTime, secondsLeft, store } from "../state";
import { clear, h, mount } from "./dom";

const IDEA_LINE: Record<string, string> = {
  locked: "Your idea is queued.",
  compiling: "Your idea is being built…",
  feasible: "Your idea was built ✓ and is on the ballot.",
  infeasible: "Your idea couldn't be built",
  timeout: "Your idea ran out of build time.",
};

export function mountNight(): () => void {
  const root = h("div", { class: "night", style: { display: "none" } });
  const remove = mount(root);
  let renderedKey = "";

  const render = () => {
    const ph = store.phase;
    const show = ph?.phase === "night";
    root.style.display = show ? "" : "none";
    if (!show) {
      renderedKey = "";
      return;
    }
    const key = JSON.stringify([ph.stage, store.nightProgress, store.vote?.ends_at, store.myVote, store.voteEnd, store.idea]);
    if (key === renderedKey) return;
    renderedKey = key;
    clear(root);
    root.append(h("h1", null, `🌙 Night ${ph.day}`));
    const st = store.idea;
    if (st && IDEA_LINE[st.status]) {
      root.append(h("div", { class: "sub" }, IDEA_LINE[st.status], st.reason ? ` — ${st.reason}` : ""));
    }
    if (ph.stage === "compile" || !ph.stage) {
      const np = store.nightProgress;
      root.append(h("div", { style: "font-size:20px" },
        np && np.total ? `Building ideas… ${np.ready}/${np.total} ready` : "No ideas were submitted today."));
      root.append(h("div", { class: "sub small" }, "The agents are turning ideas into mechanics. The world is frozen."));
    } else if (ph.stage === "vote" && store.vote) {
      const v = store.vote;
      root.append(h("div", { style: "font-size:20px" }, "Vote for tomorrow's mechanic"));
      root.append(h("div", { class: "sub small", "data-countdown": "vote" }, ""));
      const cards = h("div", { class: "cards" });
      for (const idea of v.ideas) {
        const mine = idea.id === v.your_idea_id;
        const chosen = store.myVote === idea.id;
        const btn = h("button", { class: chosen ? "primary" : "", disabled: mine }, mine ? "Your idea" : chosen ? "Voted ✓" : "Vote");
        btn.onclick = () => {
          store.myVote = idea.id;
          socket.send({ t: "vote", idea_id: idea.id });
          renderedKey = "";
          render();
        };
        cards.append(h("div", { class: `card${chosen ? " chosen" : ""}${mine ? " mine" : ""}` },
          h("div", { class: "name" }, idea.name), h("div", { class: "summary" }, idea.summary), btn));
      }
      root.append(cards);
      const abstain = h("button", { class: store.myVote === null ? "primary" : "" }, store.myVote === null ? "Abstained ✓" : "Abstain");
      abstain.onclick = () => {
        store.myVote = null;
        socket.send({ t: "vote", idea_id: null });
        renderedKey = "";
        render();
      };
      root.append(abstain);
    } else {
      const ve = store.voteEnd;
      if (ve && ve.results.length) {
        const max = Math.max(1, ...ve.results.map((r) => r.votes));
        const res = h("div", { class: "results" });
        for (const r of [...ve.results].sort((a, b) => b.votes - a.votes)) {
          res.append(h("div", { class: "r" },
            h("span", { style: "width:200px;text-align:right" }, `${r.winner ? "🏆 " : ""}${r.name}`),
            h("div", { class: "barv", style: { width: `${(r.votes / max) * 200}px`, opacity: r.winner ? "1" : ".5" } }),
            h("span", null, String(r.votes))));
        }
        root.append(res);
      } else {
        const np = store.nightProgress;
        root.append(h("div", { style: "font-size:20px" },
          np && np.total ? "The results are in." : "A quiet night."));
      }
      root.append(h("div", { class: "sub" }, "The winner goes live at dawn."));
    }
    root.append(h("div", { class: "sub small", "data-countdown": "dawn" }, ""));
    tick();
  };
  const tick = () => {
    if (store.phase?.phase !== "night") return;
    root.querySelectorAll<HTMLElement>("[data-countdown]").forEach((el) => {
      if (el.dataset.countdown === "vote" && store.vote) {
        el.textContent = `Voting closes in ${fmtTime((store.vote.ends_at - Date.now()) / 1000)} · anonymous · no self-votes`;
      } else if (el.dataset.countdown === "dawn") {
        el.textContent = `Dawn in ${fmtTime(secondsLeft())}`;
      }
    });
  };
  const iv = window.setInterval(tick, 500);
  const offs = ["phase", "night_progress", "vote_start", "vote_end", "idea_status"].map((ev) => bus.on(ev, render));
  render();
  return () => {
    window.clearInterval(iv);
    offs.forEach((f) => f());
    remove();
  };
}
