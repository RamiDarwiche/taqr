import hashlib
import json
from decimal import Decimal
from typing import Any


def _truncate(value: Any, limit: int = 4000) -> Any:
    if value is None:
        return None
    if hasattr(value, "content"):
        value = value.content
    text_value = value if isinstance(value, str) else str(value)
    if len(text_value) <= limit:
        return text_value
    return text_value[:limit] + f"... [truncated {len(text_value) - limit} chars]"


def _canonicalize_value(value: Any) -> Any:
    """Normalize a cell so hashing matches JSONB number semantics (40.0 == 40)."""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {k: _canonicalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize_value(v) for v in value]
    return value


def fingerprint_rows(rows: Any) -> str:
    """SHA-256 over a JSON-stable encoding of query result rows.

    Use this on write and on verify (re-executed SQL results). Do not re-hash
    rows loaded back from JSONB — compare against the stored fingerprint string.
    """
    payload = json.dumps(
        _canonicalize_value(rows),
        separators=(",", ":"),
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
