"""Local runner. Uses the same query function as POST /api/v1/agent/query."""

import argparse
import os

from api.auth import CurrentUser
from api.routes import run_query


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ask QueryMesh from the terminal")
    parser.add_argument("question", help="Natural language question")
    args = parser.parse_args(argv)
    user = CurrentUser(
        user_id="cli",
        role=os.environ.get("QUERYMESH_ROLE", "admin"),
        tenant_id=os.environ.get("QUERYMESH_TENANT", "local"),
    )
    result = run_query(args.question, user)
    print(f"route: {result['route']}")
    print(result["answer"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
