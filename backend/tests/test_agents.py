"""Agent providers: Claude Code headless (fake CLI) and provider selection."""
import json
import os
import stat
import sys

import pytest

from app.config import Config
from game import dsl_examples as ex
from game.pipeline.agents import make_provider
from game.pipeline.agents.anthropic_api import AnthropicAPIProvider
from game.pipeline.agents.claude_code import ClaudeCodeProvider, parse_result, response_schema
from game.pipeline.compiler import Compiler, make_compile_factory
from game.pipeline.world_summary import world_summary

FAKE_CLI = r'''#!{python}
import json, os, sys
prompt = sys.stdin.read()
log = os.environ["FAKE_CLAUDE_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "prompt": prompt}}) + "\n")
n = sum(1 for _ in open(log))
replies = json.loads(os.environ["FAKE_CLAUDE_REPLIES"])
out = replies[min(n, len(replies)) - 1]
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                  "usage": {{"input_tokens": 10, "output_tokens": 20}}, "structured_output": out}}))
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "claude"
    path.write_text(FAKE_CLI.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def set_replies(*replies):
        monkeypatch.setenv("FAKE_CLAUDE_REPLIES", json.dumps(list(replies)))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return str(path), set_replies, calls


def compiler(provider):
    return Compiler(provider, live=[], summary=world_summary([], day=1, player_count=4, map_size=48),
                    live_day=1, players=4, map_size=48, points_cap=5, max_entities=1000, sim_seconds=20)


async def test_claude_code_compiles_with_retry(fake_cli):
    bin_, replies, calls = fake_cli
    bad = {k: v for k, v in ex.APPLE_TREES.items() if k != "summary"}
    replies({"decision": "submit", "mechanic": bad}, {"decision": "submit", "mechanic": ex.APPLE_TREES})
    res = await compiler(ClaudeCodeProvider(bin=bin_, model="sonnet"))("apple trees")
    assert res.status == "ok" and res.attempts == 2, res.errors
    assert res.usage["provider"] == "claude_code" and res.usage["cost_usd"] == 0.02
    first, second = calls()
    argv = first["argv"]
    assert argv[:3] == ["-p", "--output-format", "json"]
    assert argv[argv.index("--tools") + 1] == "" and "--json-schema" in argv and "--system-prompt" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "<idea>apple trees</idea>" in first["prompt"]
    # stateless retries: the second call carries the earlier submission and the server's feedback
    assert "<previous_response" in second["prompt"] and "summary" in second["prompt"]
    assert "<idea>apple trees</idea>" in second["prompt"]


async def test_claude_code_reject(fake_cli):
    bin_, replies, _ = fake_cli
    replies({"decision": "reject", "reason": "The world is 2D."})
    res = await compiler(ClaudeCodeProvider(bin=bin_))("make the game 3D")
    assert res.status == "infeasible" and res.reason == "The world is 2D."


async def test_claude_code_missing_binary():
    res = await compiler(ClaudeCodeProvider(bin="/nonexistent/claude"))("x")
    assert res.status == "infeasible" and "configured" in res.reason


def test_parse_result_variants():
    ok = json.dumps({"subtype": "success", "is_error": False, "structured_output": {"decision": "reject",
                                                                                     "reason": "no"}})
    assert parse_result(ok, "", 0).kind == "reject"
    from_text = json.dumps({"subtype": "success", "is_error": False,
                            "result": json.dumps({"decision": "submit", "mechanic": {"id": "x"}})})
    assert parse_result(from_text, "", 0).kind == "submit"
    assert parse_result("", "Invalid API key · Please run /login", 1).kind == "unconfigured"
    err = json.dumps({"subtype": "error_during_execution", "is_error": True, "result": "overloaded"})
    assert parse_result(err, "", 1).kind == "error"
    junk = json.dumps({"subtype": "success", "is_error": False, "structured_output": {"decision": "maybe"}})
    assert parse_result(junk, "", 0).kind == "none"


def test_response_schema_is_self_contained():
    s = response_schema()
    refs = set()

    def walk(n):
        if isinstance(n, dict):
            if "$ref" in n:
                refs.add(n["$ref"].split("/")[-1])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(s)
    assert refs and refs <= set(s["$defs"])
    assert s["properties"]["decision"]["enum"] == ["submit", "reject"]


def test_provider_selection():
    assert isinstance(make_provider(Config(agent_provider="claude_code")), ClaudeCodeProvider)
    assert isinstance(make_provider(Config(agent_provider="anthropic")), AnthropicAPIProvider)
    with pytest.raises(ValueError):
        make_provider(Config(agent_provider="nope"))
    assert make_compile_factory(Config(agent_provider="nope")) is None
    assert make_compile_factory(Config(agent_provider="claude_code")) is not None


@pytest.mark.skipif(os.environ.get("RUN_LLM_TESTS") != "1", reason="set RUN_LLM_TESTS=1 to call the real CLI")
async def test_live_claude_code():
    provider = ClaudeCodeProvider(model=os.environ.get("PN_COMPILE_MODEL", "claude-sonnet-5-5"), timeout_s=180)
    res = await compiler(provider)("mushrooms sprout around the map; eating one makes you fast for 10 seconds")
    assert res.status == "ok", (res.reason, res.errors)
