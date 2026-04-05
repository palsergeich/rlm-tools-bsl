"""Server-side project registry: human-readable name -> filesystem path."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path


def _make_salt() -> str:
    return os.urandom(16).hex()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _verify_password(password: str, salt: str, stored_hash: str) -> bool:
    return hmac.compare_digest(
        _hash_password(password, salt),
        stored_hash,
    )


class RegistryCorruptedError(Exception):
    """Raised when projects.json exists but contains invalid JSON."""


def _levenshtein(a: str, b: str) -> int:
    """Pure-Python Levenshtein distance on lowercased strings."""
    a, b = a.lower(), b.lower()
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


class ProjectRegistry:
    """CRUD + fuzzy-resolve registry persisted in a JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from rlm_tools_bsl._config import get_projects_path

            path = get_projects_path()
        self._path = path
        self._lock = threading.Lock()
        self._projects: list[dict] | None = None  # lazy

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> list[dict]:
        if not self._path.is_file():
            return []
        raw = self._path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RegistryCorruptedError(f"Cannot parse {self._path}: {exc}") from exc
        if not isinstance(data, dict) or "projects" not in data:
            raise RegistryCorruptedError(f"Invalid structure in {self._path}: expected object with 'projects' key")
        return list(data["projects"])

    def _save(self, projects: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"projects": projects}, ensure_ascii=False, indent=2)
        # Atomic write: tmp -> rename; backup existing file first
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        if self._path.is_file():
            bak = self._path.with_suffix(".bak")
            # On Windows, replace() already overwrites the target
            try:
                self._path.replace(bak)
            except OSError:
                pass
        try:
            tmp.replace(self._path)
        except OSError:
            # Fallback: if replace fails (shouldn't normally), restore bak
            bak = self._path.with_suffix(".bak")
            if bak.is_file() and not self._path.is_file():
                bak.replace(self._path)
            raise
        self._projects = projects

    def _ensure_loaded(self) -> list[dict]:
        if self._projects is None:
            self._projects = self._load()
        return self._projects

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(name: str) -> str:
        return " ".join(name.split())

    @staticmethod
    def _sanitize_entry(entry: dict) -> dict:
        """Return project entry without password fields."""
        result = {k: v for k, v in entry.items() if k not in ("password_salt", "password_hash")}
        result["has_password"] = bool(entry.get("password_hash") and entry.get("password_salt"))
        return result

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_projects(self) -> list[dict]:
        with self._lock:
            return [self._sanitize_entry(p) for p in self._ensure_loaded()]

    def add(self, name: str, path: str, description: str = "", password: str | None = None) -> dict:
        with self._lock:
            projects = self._ensure_loaded()
            name = self._normalize(name)
            if not name:
                raise ValueError("Project name must not be empty")
            if not path:
                raise ValueError("Project path must not be empty")
            if not Path(path).is_dir():
                raise ValueError(f"Directory does not exist: {path}")
            if password is not None and not password:
                raise ValueError("Password must not be empty — omit to skip")
            low = name.lower()
            for p in projects:
                if p["name"].lower() == low:
                    raise ValueError(f"Project already exists: {p['name']}")
            entry: dict = {"name": name, "path": path, "description": description}
            if password:
                salt = _make_salt()
                entry["password_salt"] = salt
                entry["password_hash"] = _hash_password(password, salt)
            projects.append(entry)
            self._save(projects)
            return self._sanitize_entry(entry)

    def remove(self, name: str) -> dict:
        with self._lock:
            projects = self._ensure_loaded()
            name = self._normalize(name)
            low = name.lower()
            for i, p in enumerate(projects):
                if p["name"].lower() == low:
                    removed = projects.pop(i)
                    self._save(projects)
                    return self._sanitize_entry(removed)
            raise KeyError(f"Project not found: {name}")

    def rename(self, old_name: str, new_name: str) -> dict:
        with self._lock:
            projects = self._ensure_loaded()
            old_name = self._normalize(old_name)
            new_name = self._normalize(new_name)
            if not new_name:
                raise ValueError("New name must not be empty")
            old_low = old_name.lower()
            new_low = new_name.lower()
            target = None
            for p in projects:
                if p["name"].lower() == old_low:
                    target = p
                elif p["name"].lower() == new_low:
                    raise ValueError(f"Name already taken: {p['name']}")
            if target is None:
                raise KeyError(f"Project not found: {old_name}")
            target["name"] = new_name
            self._save(projects)
            return self._sanitize_entry(target)

    def update(
        self,
        name: str,
        path: str | None = None,
        description: str | None = None,
        password: str | None = None,
        clear_password: bool = False,
    ) -> dict:
        with self._lock:
            projects = self._ensure_loaded()
            name = self._normalize(name)
            if password is not None and clear_password:
                raise ValueError("Cannot set password and clear_password at the same time")
            if password is not None and not password:
                raise ValueError("Password must not be empty — omit to skip")
            low = name.lower()
            target = None
            for p in projects:
                if p["name"].lower() == low:
                    target = p
                    break
            if target is None:
                raise KeyError(f"Project not found: {name}")
            if path is not None:
                if not Path(path).is_dir():
                    raise ValueError(f"Directory does not exist: {path}")
                target["path"] = path
            if description is not None:
                target["description"] = description
            if password:
                salt = _make_salt()
                target["password_salt"] = salt
                target["password_hash"] = _hash_password(password, salt)
            elif clear_password:
                target.pop("password_salt", None)
                target.pop("password_hash", None)
            self._save(projects)
            return self._sanitize_entry(target)

    # ------------------------------------------------------------------
    # Resolve (three-level search)
    # ------------------------------------------------------------------

    def resolve(self, query: str) -> tuple[list[dict], str]:
        """Resolve a project query: exact -> substring -> fuzzy.

        Returns (matches, method) where method is one of:
        "exact", "substring", "fuzzy", "none".
        """
        with self._lock:
            projects = self._ensure_loaded()

        query_n = self._normalize(query)
        if not query_n:
            return ([], "none")
        query_low = query_n.lower()

        # 1. Exact match (case-insensitive)
        for p in projects:
            if p["name"].lower() == query_low:
                return ([self._sanitize_entry(p)], "exact")

        # 2. Substring match
        substr_matches = [self._sanitize_entry(p) for p in projects if query_low in p["name"].lower()]
        if substr_matches:
            return (substr_matches, "substring")

        # 3. Levenshtein fallback
        fuzzy_matches = []
        for p in projects:
            pname = p["name"]
            dist = _levenshtein(query_n, pname)
            threshold = min(int(len(pname) * 0.3), 3)
            if threshold < 1:
                threshold = 1
            if dist <= threshold:
                fuzzy_matches.append(p)
        if fuzzy_matches:
            return ([self._sanitize_entry(m) for m in fuzzy_matches], "fuzzy")

        return ([], "none")

    # ------------------------------------------------------------------
    # Password
    # ------------------------------------------------------------------

    def has_password(self, name: str) -> bool:
        """Check if project has a password configured."""
        with self._lock:
            projects = self._ensure_loaded()
        low = self._normalize(name).lower()
        for p in projects:
            if p["name"].lower() == low:
                return bool(p.get("password_hash") and p.get("password_salt"))
        return False

    def verify_password(self, name: str, password: str) -> bool:
        """Verify project password. Returns False if project has no password."""
        with self._lock:
            projects = self._ensure_loaded()
        low = self._normalize(name).lower()
        for p in projects:
            if p["name"].lower() == low:
                stored_hash = p.get("password_hash")
                salt = p.get("password_salt")
                if not stored_hash or not salt:
                    return False
                return _verify_password(password, salt, stored_hash)
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_path_registered(self, path: str) -> bool:
        resolved = str(Path(path).resolve())
        with self._lock:
            projects = self._ensure_loaded()
        for p in projects:
            if str(Path(p["path"]).resolve()) == resolved:
                return True
        return False


# ======================================================================
# Lazy singleton
# ======================================================================

_registry: ProjectRegistry | None = None
_registry_lock = threading.Lock()


def get_registry(path: Path | None = None) -> ProjectRegistry:
    """Lazy singleton. ``path=`` only for tests."""
    global _registry
    if path is not None:
        return ProjectRegistry(path)  # tests get their own instance
    with _registry_lock:
        if _registry is None:
            from rlm_tools_bsl._config import get_projects_path

            _registry = ProjectRegistry(get_projects_path())
        return _registry


def _reset_registry() -> None:
    """Reset singleton -- for integration tests and runtime config changes."""
    global _registry
    with _registry_lock:
        _registry = None
