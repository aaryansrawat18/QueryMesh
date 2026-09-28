from fastapi import APIRouter, Depends, HTTPException

from api.auth import CurrentUser, current_user
from api.policy import CAN, mentions_other_tenant
from api.schemas import ErrorResponse, QueryRequest, QueryResponse

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


def invoke_agent(question: str, user: CurrentUser):
    """Call the graph. The graph refuses a role before the SQL or ETL node starts."""
    from agents.data_agent import data_agent
    from langchain_core.messages import HumanMessage

    return data_agent.invoke(
        {
            "messages": [HumanMessage(content=question)],
            "route_response": "",
            "tenant_id": user.tenant_id,
            "role": user.role,
        }
    )


def run_query(question: str, user: CurrentUser) -> dict:
    """Run one question through the router. Shared by the CLI and the HTTP route."""
    if mentions_other_tenant(question, user.tenant_id):
        raise HTTPException(status_code=403, detail="cross-tenant query")
    try:
        result = invoke_agent(question, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"{user.role} cannot run {exc}") from exc
    answer, route = answer_from_result(result)
    if route not in CAN[user.role]:
        raise HTTPException(status_code=403, detail=f"{user.role} cannot run {route}")
    return {"answer": answer, "route": route}


@router.post(
    "/query",
    response_model=QueryResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def query_agent(
    body: QueryRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    return run_query(body.question, user)
