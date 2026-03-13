"""Disk cache for BSL file index stored in ~/.cache/rlm-tools-bsl/<hash>/."""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

from rlm_tools_bsl.format_detector import BslFileInfo

CACHE_VERSION = 1
_CACHE_BASE = pathlib.Path.home() / ".cache" / "rlm-tools-bsl"


def _cache_dir(base_path: str) -> pathlib.Path:
    h = hashlib.md5(base_path.encode()).hexdigest()[:12]
    return _CACHE_BASE / h


def _entry_to_dict(relative_path: str, info: BslFileInfo) -> dict:
    return {
        "p": relative_path,
        "c": info.category,
        "o": info.object_name,
        "m": info.module_type,
        "f": info.form_name,
        "cmd": info.command_name,
        "fe": info.is_form_module,
    }


def _dict_to_entry(d: dict) -> tuple[str, BslFileInfo]:
    return d["p"], BslFileInfo(
        relative_path=d["p"],
        category=d.get("c"),
        object_name=d.get("o"),
        module_type=d.get("m"),
        form_name=d.get("f"),
        command_name=d.get("cmd"),
        is_form_module=d.get("fe", False),
    )


def _paths_hash(paths: list[str]) -> str:
    """MD5 hash of sorted relative paths for cache invalidation."""
    joined = "\n".join(sorted(paths))
    return hashlib.md5(joined.encode()).hexdigest()


def load_index(
    base_path: str,
    bsl_count: int,
    bsl_paths: list[str] | None = None,
) -> list[tuple[str, BslFileInfo]] | None:
    """Load index from disk if version, bsl_count, and paths_hash match. Returns None on miss."""
    index_file = _cache_dir(base_path) / "file_index.json"
    try:
        with open(index_file, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != CACHE_VERSION:
            return None
        if data.get("bsl_count") != bsl_count:
            return None
        if bsl_paths is not None and "paths_hash" in data:
            if data["paths_hash"] != _paths_hash(bsl_paths):
                return None
        return [_dict_to_entry(e) for e in data["entries"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def save_index(
    base_path: str,
    bsl_count: int,
    entries: list[tuple[str, BslFileInfo]],
) -> None:
    """Save index to disk. Silently ignores write errors."""
    try:
        cache_dir = _cache_dir(base_path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        paths = [p for p, _ in entries]
        data = {
            "version": CACHE_VERSION,
            "base_path": base_path,
            "bsl_count": bsl_count,
            "paths_hash": _paths_hash(paths),
            "saved_at": time.time(),
            "entries": [_entry_to_dict(p, i) for p, i in entries],
        }
        with open(cache_dir / "file_index.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass
