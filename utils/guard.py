"""Path jail and URL allowlist for ETL. Fail closed."""

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

_PROJECT = Path(__file__).resolve().parents[1]
_ROOTS = ("data/extract", "data/transform")
_METADATA_HOSTS = {"metadata.google.internal", "metadata.internal"}


def jail_path(user_path: str) -> Path:
    """Resolve a relative path under data/extract or data/transform."""
    if not isinstance(user_path, str) or not user_path.strip():
        raise ValueError("path required")
    if user_path.startswith(("\\\\", "//")):
        raise ValueError("absolute paths are not allowed")
    raw = Path(user_path)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("path escapes the data directory")
    candidate = (_PROJECT / raw).resolve()
    for name in _ROOTS:
        root = (_PROJECT / name).resolve()
        if candidate == root or root in candidate.parents:
            return candidate
    raise ValueError("path must stay under data/extract or data/transform")


def allowlist_prefixes() -> list[str]:
    raw = os.environ.get("ETL_URL_ALLOWLIST", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def assert_url_allowed(url: str, prefixes: list[str] | None = None, resolve=None) -> str:
    """Accept only http(s) URLs on the allowlist whose addresses are public."""
    prefixes = allowlist_prefixes() if prefixes is None else prefixes
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError("only http(s) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs with credentials are not allowed")
    if host in _METADATA_HOSTS:
        raise ValueError("metadata hosts are not allowed")
    matched = False
    for prefix in prefixes:
        pref = urlparse(prefix)
        if url.startswith(prefix) and host == (pref.hostname or "").lower().rstrip("."):
            matched = True
            break
    if not matched:
        raise ValueError("URL is not allowlisted")
    lookup = resolve or _resolve
    try:
        addresses = lookup(host)
    except OSError as exc:
        raise ValueError("URL host did not resolve") from exc
    if not addresses or any(_blocked(ip) for ip in addresses):
        raise ValueError("URL resolves to a blocked address")
    return url


def _resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return list({item[4][0] for item in infos})
