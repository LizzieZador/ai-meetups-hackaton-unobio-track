"""Shared on-disk JSON cache for every source module.

Per the plan: cache each API's response to JSON on first call and use the cache
for demos — never rely on a live connection on stage.
"""
import json
from pathlib import Path
from typing import Callable, Optional

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def cached(source: str, key: str, fetch_fn: Callable[[], Optional[list]]) -> Optional[list]:
    """Return the cached result for (source, key) if present, else call fetch_fn(),
    cache a non-None result, and return it."""
    path = CACHE_DIR / f"{source}_{_slug(key)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    result = fetch_fn()
    if result is not None:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _slug(key: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in key.lower())[:80]
