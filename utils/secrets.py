"""Runtime config. Local reads `.env`. Production reads the process environment or a secret mount."""

import os
from pathlib import Path

from dotenv import load_dotenv

_REQUIRED = (
    "JWT_SECRET",
    "ANALYTICS_HOST",
    "ANALYTICS_USER",
    "ANALYTICS_PASSWORD",
    "ANALYTICS_DATABASE",
)
# Compose defaults and the example file. Production must replace them.
_WEAK = {
    "",
    "postgres",
    "password",
    "potgres",
    "blabla",
    "changeme",
    "querymesh_local",
    "querymesh_ro_local",
    "local-dev-jwt-secret-not-for-production",
}


def image_root() -> Path:
    return Path(os.environ.get("QUERYMESH_IMAGE_ROOT", "/app"))


def load_mounted_secrets() -> None:
    raw = os.environ.get("QUERYMESH_SECRETS_DIR", "").strip()
    if not raw:
        return
    folder = Path(raw)
    if not folder.is_dir():
        raise RuntimeError(f"QUERYMESH_SECRETS_DIR is not a directory: {raw}")
    for path in sorted(folder.iterdir()):
        if path.is_file() and not path.name.startswith("."):
            os.environ[path.name] = path.read_text(encoding="utf-8").strip()


def assert_production_secrets() -> None:
    baked = image_root() / ".env"
    if baked.is_file():
        raise RuntimeError("refusing to start: .env is baked into the image")
    missing = [name for name in _REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("missing production secrets: " + ", ".join(missing))
    jwt_secret = os.environ["JWT_SECRET"]
    if jwt_secret in _WEAK or len(jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET is not a production secret")
    password = os.environ["ANALYTICS_PASSWORD"]
    if password in _WEAK or len(password) < 12:
        raise RuntimeError("ANALYTICS_PASSWORD is not a production secret")


def prepare_runtime() -> None:
    """Load local dotenv, or fail closed when QUERYMESH_ENV=production."""
    if os.environ.get("QUERYMESH_ENV", "local").strip() == "production":
        load_mounted_secrets()
        assert_production_secrets()
        return
    load_dotenv()
