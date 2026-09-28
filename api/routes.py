import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import CurrentUser
from api.jobs import limited_user
from api.policy import CAN, mentions_other_tenant
from api.schemas import ErrorResponse, QueryRequest, QueryResponse
from utils.audit import record
from utils.budget import BudgetExceeded, begin
from utils.cache import get_answer, put_answer
from utils.guardrails import assert_untrusted
from utils.metrics import metrics
from utils.reliability import CircuitOpen
from utils.trace import current_request_id, set_request_id, span

logger = logging.getLogger("querymesh.api")

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _text(message) -> str:
    if message is None:
        return ""
    if isinstance(message, dict):
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")
    else:
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        return ""
    if isinstance(content, str):
        return content.strip()
    return str(content).strip() if content else ""


def answer_from_result(result) -> tuple[str, str]:
    """Pull the user-facing answer and route out of a data_agent result."""
    if hasattr(result, "model_dump"):
        data = result.model_dump()
    elif isinstance(result, dict):
        data = result
    else:
        data = {
            "route_response": getattr(result, "route_response", ""),
            "messages": getattr(result, "messages", []),
        }

    route = data.get("route_response") or ""
    if route not in ("sql", "etl"):
        raise ValueError(f"Invalid route response: {route}")

    messages = data.get("messages") or []
    last = messages[-1] if messages else None

    if route == "sql" and last is not None:
        final = last.get("final_answer") if isinstance(last, dict) else getattr(last, "final_answer", None)
        if final:
            return str(final), route

    if route == "etl" and last is not None:
        inner = last.get("messages") if isinstance(last, dict) else getattr(last, "messages", None)
        if inner:
            for message in reversed(list(inner)):
                text = _text(message)
                if text:
                    return text, route

    text = _text(last)
    if not text:
        raise ValueError("Agent finished without an answer")
    return text, route


def needs_approval_from_result(result) -> bool:
    if hasattr(result, "model_dump"):
        data = result.model_dump()
    elif isinstance(result, dict):
        data = result
    else:
        data = {"needs_approval": getattr(result, "needs_approval", False), "messages": getattr(result, "messages", [])}
    if data.get("needs_approval"):
        return True
    messages = data.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, dict):
        return bool(last.get("needs_approval"))
    return bool(getattr(last, "needs_approval", False))


def _artifacts(result) -> tuple[str, list[str]]:
    if hasattr(result, "model_dump"):
        data = result.model_dump()
    elif isinstance(result, dict):
        data = result
    else:
        data = {"messages": getattr(result, "messages", [])}
    messages = data.get("messages") or []
    last = messages[-1] if messages else None
    sql = ""
    tools: list[str] = []
    if isinstance(last, dict):
        sql = str(last.get("generated_sql_query") or "")
        inner = last.get("messages") or []
    else:
        sql = str(getattr(last, "generated_sql_query", "") or "")
        inner = getattr(last, "messages", None) or []
    for message in inner:
        calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
        for call in calls or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            if name:
                tools.append(str(name))
    return sql, tools


def invoke_agent(question: str, user: CurrentUser):
    """Call the graph. The graph refuses a role before the SQL or ETL node starts."""
    from agents.data_agent import data_agent
    from langchain_core.messages import HumanMessage

    request_id = current_request_id()
    with span("agent"):
        return data_agent.invoke(
            {
                "messages": [HumanMessage(content=question)],
                "route_response": "",
                "tenant_id": user.tenant_id,
                "role": user.role,
            },
            config={
                "recursion_limit": int(os.environ.get("MAX_GRAPH_STEPS", "16")),
                "run_name": "data_agent",
                "metadata": {"request_id": request_id},
                "tags": [f"request:{request_id}"] if request_id else [],
            },
        )


def run_query(question: str, user: CurrentUser) -> dict:
    """Run one question through the router. Shared by the CLI and the HTTP route."""
    if mentions_other_tenant(question, user.tenant_id):
        raise HTTPException(status_code=403, detail="cross-tenant query")
    try:
        assert_untrusted(question, "user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cached = get_answer(user.tenant_id, user.role, question)
    if cached:
        return cached
    try:
        result = invoke_agent(question, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"{user.role} cannot run {exc}") from exc
    except BudgetExceeded as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CircuitOpen as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    answer, route = answer_from_result(result)
    if route not in CAN[user.role]:
        raise HTTPException(status_code=403, detail=f"{user.role} cannot run {route}")
    sql, tools = _artifacts(result)
    payload = {
        "answer": answer,
        "route": route,
        "needs_approval": needs_approval_from_result(result),
        "sql": sql,
        "tools": tools,
    }
    put_answer(user.tenant_id, user.role, question, payload)
    return payload


@router.post(
    "/query",
    response_model=QueryResponse,
    response_model_exclude_defaults=True,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def query_agent(
    body: QueryRequest,
    request: Request,
    user: CurrentUser = Depends(limited_user),
) -> dict:
    set_request_id(getattr(request.state, "request_id", "") or current_request_id())
    budget = begin()
    status = "error"
    payload: dict = {}
    try:
        payload = run_query(body.question, user)
        status = "needs_approval" if payload.get("needs_approval") else "ok"
        return payload
    except HTTPException as exc:
        status = f"http_{exc.status_code}"
        raise
    except BudgetExceeded as exc:
        status = "http_400"
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        metrics.observe_llm(budget.tokens, budget.cost_usd)
        metrics.observe_outcome(status)
        if payload.get("route"):
            metrics.observe_route(payload["route"])
        record(
            request_id=getattr(request.state, "request_id", ""),
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            question=body.question,
            sql=str(payload.get("sql") or ""),
            tools=list(payload.get("tools") or []),
            status=status,
        )
        logger.info(
            json.dumps(
                {
                    "request_id": getattr(request.state, "request_id", ""),
                    "event": "llm_usage",
                    "tokens": budget.tokens,
                    "cost_usd": round(budget.cost_usd, 6),
                    "steps": budget.steps,
                }
            )
        )
