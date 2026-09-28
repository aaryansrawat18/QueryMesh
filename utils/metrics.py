"""In-process counters for /metrics. Prometheus text, no extra client library."""

import math
import threading


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.requests: dict[str, int] = {}
        self.latency_ms: list[float] = []
        self.tokens = 0
        self.cost_usd = 0.0
        self.sql_ms = 0.0
        self.sql_calls = 0
        self.routes: dict[str, int] = {}
        self.outcomes: dict[str, int] = {}

    def observe_http(self, status: int, latency_ms: float) -> None:
        with self._lock:
            key = str(status)
            self.requests[key] = self.requests.get(key, 0) + 1
            self.latency_ms.append(float(latency_ms))
            if len(self.latency_ms) > 512:
                del self.latency_ms[:-512]

    def observe_llm(self, tokens: int, cost_usd: float) -> None:
        with self._lock:
            self.tokens += int(tokens)
            self.cost_usd += float(cost_usd)

    def observe_sql(self, latency_ms: float) -> None:
        with self._lock:
            self.sql_ms += float(latency_ms)
            self.sql_calls += 1

    def observe_outcome(self, status: str) -> None:
        with self._lock:
            self.outcomes[status] = self.outcomes.get(status, 0) + 1

    def observe_route(self, route: str) -> None:
        if route not in ("sql", "etl"):
            return
        with self._lock:
            self.routes[route] = self.routes.get(route, 0) + 1

    def render(self) -> str:
        with self._lock:
            requests = dict(self.requests)
            latency = list(self.latency_ms)
            tokens = self.tokens
            cost = self.cost_usd
            sql_ms = self.sql_ms
            sql_calls = self.sql_calls
            routes = dict(self.routes)
            outcomes = dict(self.outcomes)
        total = sum(requests.values())
        finished = sum(outcomes.values())
        success = (outcomes.get("ok", 0) / finished) if finished else 0.0
        errors = sum(count for status, count in requests.items() if int(status) >= 500)
        rate = (errors / total) if total else 0.0
        lines = [
            "# HELP querymesh_http_requests_total HTTP requests by status",
            "# TYPE querymesh_http_requests_total counter",
        ]
        for status, count in sorted(requests.items()):
            lines.append(f'querymesh_http_requests_total{{status="{status}"}} {count}')
        lines += [
            "# HELP querymesh_http_errors_total HTTP 5xx responses",
            "# TYPE querymesh_http_errors_total counter",
            f"querymesh_http_errors_total {errors}",
            "# HELP querymesh_error_rate Share of HTTP responses that are 5xx",
            "# TYPE querymesh_error_rate gauge",
            f"querymesh_error_rate {rate:.6f}",
            "# HELP querymesh_http_latency_ms Request latency quantiles",
            "# TYPE querymesh_http_latency_ms gauge",
            f'querymesh_http_latency_ms{{quantile="0.5"}} {_quantile(latency, 0.5):.2f}',
            f'querymesh_http_latency_ms{{quantile="0.95"}} {_quantile(latency, 0.95):.2f}',
            "# HELP querymesh_llm_tokens_total Tokens charged to request budgets",
            "# TYPE querymesh_llm_tokens_total counter",
            f"querymesh_llm_tokens_total {tokens}",
            "# HELP querymesh_llm_cost_usd Estimated model spend",
            "# TYPE querymesh_llm_cost_usd counter",
            f"querymesh_llm_cost_usd {cost:.6f}",
            "# HELP querymesh_sql_duration_ms_sum SQL execution time",
            "# TYPE querymesh_sql_duration_ms_sum counter",
            f"querymesh_sql_duration_ms_sum {sql_ms:.2f}",
            "# HELP querymesh_sql_calls_total SQL executions",
            "# TYPE querymesh_sql_calls_total counter",
            f"querymesh_sql_calls_total {sql_calls}",
            "# HELP querymesh_success_rate Share of agent queries that completed",
            "# TYPE querymesh_success_rate gauge",
            f"querymesh_success_rate {success:.6f}",
            "# HELP querymesh_agent_routes_total Completed agent routes",
            "# TYPE querymesh_agent_routes_total counter",
        ]
        for route, count in sorted(routes.items()):
            lines.append(f'querymesh_agent_routes_total{{route="{route}"}} {count}')
        return "\n".join(lines) + "\n"


metrics = Metrics()
