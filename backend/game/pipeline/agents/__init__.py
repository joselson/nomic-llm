"""Pluggable agent providers for the night compiler.

Select one with PN_AGENT_PROVIDER (see app/config.py). To add a provider, implement
base.AgentProvider and register a `from_config(cfg)` constructor in PROVIDERS.
"""
from __future__ import annotations

from typing import Callable

from .base import AgentProvider, AgentReply, AgentSession

PROVIDERS: dict[str, Callable] = {}


def _register() -> None:
    from .anthropic_api import AnthropicAPIProvider
    from .claude_code import ClaudeCodeProvider
    PROVIDERS.update({AnthropicAPIProvider.name: AnthropicAPIProvider.from_config,
                      ClaudeCodeProvider.name: ClaudeCodeProvider.from_config})


def make_provider(cfg) -> AgentProvider:
    if not PROVIDERS:
        _register()
    try:
        factory = PROVIDERS[cfg.agent_provider]
    except KeyError:
        raise ValueError(f"unknown agent provider {cfg.agent_provider!r}; choose one of {sorted(PROVIDERS)}")
    return factory(cfg)


__all__ = ["AgentProvider", "AgentReply", "AgentSession", "PROVIDERS", "make_provider"]
