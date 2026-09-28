"""Top-K schema retrieval. Hash embeddings stay local; no extra model call."""

import hashlib
import math
import os
import re

from utils.guardrails import assert_untrusted

# ponytail: token hashing, not a hosted embedding model. Replace embed() if you add one.
_DIM = 128
_TOKEN = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def embed(text: str) -> list[float]:
    vector = [0.0] * _DIM
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:2], "big") % _DIM
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _score(question: str, doc: dict) -> float:
    q_tokens = set(_tokens(question))
    overlap = len(q_tokens & set(_tokens(doc.get("text", ""))))
    q_vec = embed(question)
    d_vec = embed(doc.get("text", ""))
    cosine = sum(left * right for left, right in zip(q_vec, d_vec))
    return overlap * 10 + cosine


def top_k(question: str, docs: list[dict], k: int | None = None) -> list[dict]:
    """Highest-scoring tables, then a direct foreign-key neighbor if it still fits in K."""
    limit = int(os.environ.get("SCHEMA_RAG_K", "4")) if k is None else k
    if limit < 1 or not docs:
        return []
    ranked = sorted(docs, key=lambda doc: _score(question, doc), reverse=True)
    by_name = {doc["name"]: doc for doc in docs}
    picked: list[dict] = []
    seen: set[str] = set()
    for doc in ranked:
        if len(picked) >= limit:
            break
        if doc["name"] not in seen:
            picked.append(doc)
            seen.add(doc["name"])
        for ref in doc.get("refs") or []:
            if len(picked) >= limit:
                break
            if ref in by_name and ref not in seen:
                picked.append(by_name[ref])
                seen.add(ref)
    return picked


def render(docs: list[dict]) -> str:
    return "\n".join(doc.get("text", "") for doc in docs)


def schema_slice(question: str, docs: list[dict], k: int | None = None) -> str:
    """Rendered top-K tables only. Raises if a retrieved fragment tries to inject instructions."""
    text = render(top_k(question, docs, k))
    assert_untrusted(text, "schema")
    return text


def cached_schema(question: str, role: str, loader, client, k: int | None = None) -> str:
    """Reuse a recent slice for the same role and question. `loader` hits the catalog once per miss."""
    limit = int(os.environ.get("SCHEMA_RAG_K", "4")) if k is None else k
    raw = f"{role}\n{limit}\n{question.strip()}"
    key = "qm:schema:" + hashlib.sha256(raw.encode()).hexdigest()
    try:
        hit = client.get(key)
    except Exception:
        hit = None
    if hit:
        return assert_untrusted(hit, "schema")
    text = schema_slice(question, loader(), k)
    ttl = int(os.environ.get("SCHEMA_CACHE_TTL", "120"))
    if ttl > 0:
        try:
            client.set(key, text, ex=ttl)
        except Exception:
            return text
    return text
