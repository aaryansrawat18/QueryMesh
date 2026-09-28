"""Print a one-hour HS256 token. This is not an API endpoint."""

import argparse
import os
import time

import jwt
from dotenv import load_dotenv

load_dotenv()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sign a QueryMesh access token")
    parser.add_argument("--sub", default="user_42")
    parser.add_argument("--role", default="analyst", choices=["admin", "analyst", "viewer"])
    parser.add_argument("--tenant", default="local")
    args = parser.parse_args(argv)
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise SystemExit("JWT_SECRET is not set")
    token = jwt.encode(
        {
            "sub": args.sub,
            "role": args.role,
            "tenant_id": args.tenant,
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
