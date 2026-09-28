import os
from dataclasses import dataclass

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)
ROLES = ("admin", "analyst", "viewer")


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    role: str
    tenant_id: str


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="missing bearer token")
    load_dotenv()
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET is not set")
    try:
        payload = jwt.decode(creds.credentials, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")

    role = payload.get("role")
    tenant_id = payload.get("tenant_id")
    user_id = payload.get("sub")
    if role not in ROLES or not tenant_id or not user_id:
        raise HTTPException(status_code=401, detail="token missing required claims")
    return CurrentUser(user_id=str(user_id), role=role, tenant_id=str(tenant_id))
