"""Per-request step and token budget. One context is active per call."""

import os
from contextvars import ContextVar


class BudgetExceeded(RuntimeError):
    pass


def _tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    content = getattr(value, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    return str(value)


class Budget:
    def __init__(self):
        self.max_tokens = int(os.environ.get("MAX_TOKENS", "8000"))
        self.max_steps = int(os.environ.get("MAX_AGENT_STEPS", "8"))
        self.tokens = 0
        self.steps = 0
        self.cost_usd = 0.0

    def step(self) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise BudgetExceeded("agent step budget exceeded")

    def charge(self, text: str, *, incoming: bool) -> None:
        count = _tokens(_as_text(text))
        if self.tokens + count > self.max_tokens:
            raise BudgetExceeded("token budget exceeded")
        self.tokens += count
        rate_key = "LLM_COST_PER_1K_INPUT" if incoming else "LLM_COST_PER_1K_OUTPUT"
        default = "0.00015" if incoming else "0.0006"
        self.cost_usd += count / 1000 * float(os.environ.get(rate_key, default))


_current: ContextVar[Budget | None] = ContextVar("querymesh_budget", default=None)


def begin() -> Budget:
    budget = Budget()
    _current.set(budget)
    return budget


def active() -> Budget:
    budget = _current.get()
    if budget is None:
        budget = Budget()
        _current.set(budget)
    return budget


class Metered:
    """Counts one model call against the active budget, then delegates."""

    def __init__(self, inner, model: str = "", fallback: bool = False):
        self.inner = inner
        self.model = model
        self.fallback = fallback

    def invoke(self, prompt, **kwargs):
        from utils.reliability import CircuitOpen, call, llm_breaker, llm_retryable
        from utils.trace import annotate, span

        budget = active()
        budget.step()
        budget.charge(prompt, incoming=True)
        attempts = int(os.environ.get("LLM_RETRIES", "2"))
        with span("llm", model=self.model or "unknown") as current:
            try:
                result = call(
                    llm_breaker,
                    lambda: self.inner.invoke(prompt, **kwargs),
                    attempts=attempts,
                    retryable=llm_retryable,
                )
            except (BudgetExceeded, CircuitOpen):
                raise
            except Exception as exc:
                if self.fallback or not llm_retryable(exc):
                    raise
                from utils.llm_pick import fallback_llm

                alt = fallback_llm()
                if not alt.model or alt.model == self.model:
                    raise
                budget.charge(prompt, incoming=True)
                result = call(
                    llm_breaker,
                    lambda: alt.inner.invoke(prompt, **kwargs),
                    attempts=attempts,
                    retryable=llm_retryable,
                )
            self._tally(budget, prompt, result)
            calls = getattr(result, "tool_calls", None) or []
            names = [item.get("name", "") for item in calls if isinstance(item, dict) and item.get("name")]
            annotate(current, "tool_calls", ",".join(names))
            annotate(current, "tokens", budget.tokens)
            return result

    def _tally(self, budget, prompt, result) -> None:
        usage = getattr(result, "usage_metadata", None) or {}
        if usage.get("input_tokens") or usage.get("output_tokens"):
            # Replace the char estimate with provider counts when the model reports them.
            estimated = _tokens(_as_text(prompt)) + _tokens(_as_text(result))
            reported = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
            budget.tokens += reported - estimated
            if budget.tokens > budget.max_tokens:
                raise BudgetExceeded("token budget exceeded")
        else:
            budget.charge(result, incoming=False)

    def with_structured_output(self, schema):
        return Metered(self.inner.with_structured_output(schema), model=self.model, fallback=self.fallback)

    def bind_tools(self, tools):
        return Metered(self.inner.bind_tools(tools), model=self.model, fallback=self.fallback)
