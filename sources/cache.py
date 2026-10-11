"""Shared on-disk JSON cache for every source module.

Per the plan: cache each API's response to JSON on first call and use the cache
for demos — never rely on a live connection on stage.

Sources are fetched in parallel threads, so writes go to a temp file and are
swapped in atomically — a concurrent reader never sees a half-written file.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def cached(source: str, key: str, fetch_fn: Callable[[], Optional[list]]) -> Optional[list]:
    """Return the cached result for (source, key) if present, else call fetch_fn(),
    cache a non-None result, and return it."""
    path = CACHE_DIR / f"{source}_{_slug(key)}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # corrupt entry — refetch and overwrite
            pass

    result = fetch_fn()
    if result is not None:
        atomic_write(path, json.dumps(result, ensure_ascii=False, indent=2))
    return result


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _slug(key: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in key.lower())[:80]
