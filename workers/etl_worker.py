"""ETL worker. Requires REDIS_URL so it sees the same queue as the API.

    python -m workers.etl_worker
"""

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

from utils.jobs import process_next
from utils.runtime import get_backends


def run_etl(job: dict) -> str:
    from agents.etl_analyst import etl_analyst
    from langchain_core.messages import HumanMessage

    response = etl_analyst.invoke({"messages": [HumanMessage(content=job["question"])]})
    messages = response.get("messages") if isinstance(response, dict) else response.messages
    for message in reversed(list(messages)):
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        if isinstance(content, str) and content.strip() and not getattr(message, "tool_calls", None):
            return content.strip()
    return "ETL job finished"


def main() -> int:
    load_dotenv()
    if not os.environ.get("REDIS_URL", "").strip():
        print("REDIS_URL is required for the ETL worker", file=sys.stderr)
        return 1
    os.environ["QUERYMESH_WORKER"] = "1"
    jobs, _ = get_backends()
    while True:
        process_next(jobs, run_etl, timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
