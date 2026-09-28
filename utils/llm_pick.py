"""Env-driven model choice. Router and easy SQL stay on the cheap model."""

import os
import re

from utils.budget import Metered

_ENV = {
    "low": "LLM_LOW",
    "medium": "LLM_MEDIUM",
    "high": "LLM_HIGH",
    "claude": "LLM_HIGH",  # old tier name; same model as high
}
_DEFAULTS = {
    "low": "gpt-4o-mini",
    "medium": "gpt-4o",
    "high": "claude-sonnet-4-5",
    "claude": "claude-sonnet-4-5",
}
_HARD_SQL = re.compile(
    r"\b(join|window|rank|percentile|cohort|median|year[- ]over[- ]year)\b",
    re.I,
)


def model_name(level: str) -> str:
    key = _ENV.get(level)
    if key is None:
        raise ValueError(f"unknown model level: {level}")
    name = os.environ.get(key, "").strip() or _DEFAULTS[level]
    return name


def sql_level(question: str) -> str:
    """Stronger SQL model only when the question looks like a hard query."""
    if _HARD_SQL.search(question or ""):
        return "medium"
    return "low"


def _build(name: str, *, fallback: bool) -> Metered:
    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "30"))
    if name.startswith("claude"):
        from langchain_anthropic import ChatAnthropic

        inner = ChatAnthropic(model=name, temperature=0, timeout=timeout)
    else:
        from langchain_openai import ChatOpenAI

        inner = ChatOpenAI(model=name, temperature=0, timeout=timeout)
    return Metered(inner, model=name, fallback=fallback)


def pick_llm(level: str) -> Metered:
    return _build(model_name(level), fallback=False)


def fallback_llm() -> Metered:
    """Used after the primary model keeps failing. Same call budget, no second fallback."""
    name = os.environ.get("LLM_FALLBACK", "").strip() or "gpt-4o-mini"
    return _build(name, fallback=True)
