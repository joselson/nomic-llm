# Patch Notes

A competitive multiplayer browser game where **players invent the game while playing it**. Every in-game day each player submits an idea; at night Claude compiles the ideas into mechanics (pure data in a DSL), everyone votes anonymously, and the winning mechanic goes live the next day. First to 100 points wins.

The design brief is [`SPEC.md`](SPEC.md). The mechanics DSL lives in [`backend/game/dsl.py`](backend/game/dsl.py).

## Quick start (Docker)

```bash
claude setup-token                       # once, on your machine: prints a long-lived token for your Claude subscription
export CLAUDE_CODE_OAUTH_TOKEN=...       # or: export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
# open http://localhost:8000, create a room, share the invite link
```

Without credentials the game still runs; every idea is reported back as "compiler not configured" and nights are quiet.

## Night compiler providers

The compiler's model calls go through a pluggable provider (`backend/game/pipeline/agents/`), chosen with `PN_AGENT_PROVIDER`:

| Provider | How it calls Claude | Credentials |
|---|---|---|
| `claude_code` (default) | Claude Code headless: `claude -p --output-format json --json-schema …`, one stateless call per attempt | whatever the CLI is logged in with: `claude` login, `CLAUDE_CODE_OAUTH_TOKEN`, or `ANTHROPIC_API_KEY` |
| `anthropic` | Anthropic Messages API with `submit_mechanic` / `reject_idea` tools, prompt caching, refusal fallback | `ANTHROPIC_API_KEY` |

Both share the same retry loop (validate → simulate → feed errors back). To add a provider, implement `AgentProvider` (`session()` returning an object with `async send(text) -> AgentReply`) and register it in `agents/__init__.py`.

Note: a Claude subscription is meant for your own use. If you host games for other people, switch to `PN_AGENT_PROVIDER=anthropic` with an API key.

## Development

```bash
# backend (Python 3.12+)
cd backend
pip install -e ".[dev]"
DEBUG=1 uvicorn app.main:app --reload --port 8000

# frontend (Node 22)
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api and /ws to :8000
```

`npm run build` writes `frontend/dist`, which the backend serves at `/` when present.

### Tests

```bash
cd backend
python -m pytest                 # engine, scenarios, protocol, rooms, pipeline (Claude mocked)
RUN_LLM_TESTS=1 python -m pytest -k live   # real compile runs (Claude Code CLI and/or API key)
python -m game.dsl_examples      # DSL self-test; regenerates game/mechanic.schema.json

cd ../frontend && npm run typecheck
```

### Debug tools (`DEBUG=1`)

The in-game debug panel and these endpoints are enabled only with `DEBUG=1`:

| Endpoint | Body | Effect |
|---|---|---|
| `POST /api/rooms/{code}/debug/load` | `{"example": "apple_trees"}` or `{"mechanic": {...}}` | Validate and deploy a mechanic now |
| `POST /api/rooms/{code}/debug/skip` | — | End the current phase |
| `POST /api/rooms/{code}/debug/points` | `{"player_id": "p1", "amount": 10}` | Give points (bypasses the ledger) |
| `POST /api/rooms/{code}/debug/ff` | `{"seconds": 60}` | Fast-forward the world |

## Configuration

Every field of [`backend/app/config.py`](backend/app/config.py) can be overridden with `PN_<NAME>`, e.g. `PN_PLAY_SECONDS=120`, `PN_AGENT_PROVIDER=claude_code`, `PN_COMPILE_MODEL=claude-sonnet-5-5`, `PN_AGENT_MAX_PARALLEL=4`, `PN_COMPILE_EFFORT=medium`, `PN_DATA_DIR=/data`. The host can also change day lengths, max days and the win target in the lobby.

The compiler logs every idea (text, outcome, attempts, errors, final JSON, votes, token usage) to `data/ideas.jsonl` — the dataset for deciding which DSL primitives to add next.

## Layout

```
backend/
  app/        FastAPI app, WebSocket protocol, rooms + phase state machine, config
  game/
    dsl.py            mechanics schema, validator, patches (source of truth)
    engine/           interpreter: state, world/tick, events, exprs, effects, targets,
                      limits + points ledger, physics, behaviors, support.py
    pipeline/         night: compile (Claude) -> validate -> simulate -> vote -> deploy
  tests/
frontend/
  src/net/socket.ts   protocol types (mirror of backend/app/protocol.py)
  src/scenes/         Join, Lobby, World (Phaser 3)
  src/render/         DSL visuals, sprites, client prediction
  src/ui/             DOM overlay: HUD, bars, idea box, prompts, night/vote, patch notes
```

## Status

All five MVP milestones from the spec are implemented: skeleton, engine core (every effect, trigger, op and behavior; `support.py` sets are empty), rendering and UI, the night pipeline, and endgame (win detection, max days, reconnect, Docker). Not yet verified against the live API from this repo's CI: the compile prompt quality and night timing with 8 real ideas — run the live test and a playtest with a key set.
