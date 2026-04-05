"""Unit tests for the project registry (projects.py)."""

import json
import os
from unittest.mock import patch

import pytest

from rlm_tools_bsl.projects import (
    ProjectRegistry,
    RegistryCorruptedError,
    _levenshtein,
    _reset_registry,
    get_registry,
)


# ---------------------------------------------------------------------------
# _levenshtein
# ---------------------------------------------------------------------------


def test_levenshtein_identical():
    assert _levenshtein("abc", "abc") == 0


def test_levenshtein_empty():
    assert _levenshtein("", "abc") == 3
    assert _levenshtein("abc", "") == 3


def test_levenshtein_known_pairs():
    assert _levenshtein("kitten", "sitting") == 3
    assert _levenshtein("abc", "acb") == 2


def test_levenshtein_case_insensitive():
    assert _levenshtein("ABC", "abc") == 0


# ---------------------------------------------------------------------------
# ProjectRegistry -- basic CRUD
# ---------------------------------------------------------------------------


def test_empty_registry(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    assert reg.list_projects() == []


def test_add_and_list(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    entry = reg.add("Test Project", str(d), "A test")
    assert entry["name"] == "Test Project"
    assert entry["path"] == str(d)
    assert entry["description"] == "A test"
    projects = reg.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "Test Project"


def test_add_duplicate_name(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    with pytest.raises(ValueError, match="already exists"):
        reg.add("alpha", str(d))  # case-insensitive


def test_add_nonexistent_dir(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    with pytest.raises(ValueError, match="does not exist"):
        reg.add("Bad", str(tmp_path / "no_such_dir"))


def test_add_empty_name(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    with pytest.raises(ValueError, match="must not be empty"):
        reg.add("", str(d))


def test_add_empty_path(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    with pytest.raises(ValueError, match="must not be empty"):
        reg.add("Name", "")


def test_remove(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    removed = reg.remove("alpha")
    assert removed["name"] == "Alpha"
    assert reg.list_projects() == []


def test_remove_not_found(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    with pytest.raises(KeyError, match="not found"):
        reg.remove("Ghost")


def test_rename(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    renamed = reg.rename("Alpha", "Beta")
    assert renamed["name"] == "Beta"
    assert reg.list_projects()[0]["name"] == "Beta"


def test_rename_to_taken_name(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d1 = tmp_path / "s1"
    d1.mkdir()
    d2 = tmp_path / "s2"
    d2.mkdir()
    reg.add("Alpha", str(d1))
    reg.add("Beta", str(d2))
    with pytest.raises(ValueError, match="already taken"):
        reg.rename("Alpha", "beta")


def test_rename_not_found(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    with pytest.raises(KeyError, match="not found"):
        reg.rename("Ghost", "New")


def test_update_path(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d1 = tmp_path / "s1"
    d1.mkdir()
    d2 = tmp_path / "s2"
    d2.mkdir()
    reg.add("Alpha", str(d1))
    updated = reg.update("Alpha", path=str(d2))
    assert updated["path"] == str(d2)


def test_update_description(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), "old")
    updated = reg.update("Alpha", description="new desc")
    assert updated["description"] == "new desc"


def test_update_nonexistent_dir(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    with pytest.raises(ValueError, match="does not exist"):
        reg.update("Alpha", path=str(tmp_path / "nope"))


def test_update_not_found(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    with pytest.raises(KeyError, match="not found"):
        reg.update("Ghost", description="x")


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------


def test_resolve_exact(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Main Config", str(d))
    matches, method = reg.resolve("Main Config")
    assert method == "exact"
    assert len(matches) == 1
    assert matches[0]["name"] == "Main Config"


def test_resolve_exact_case_insensitive(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Main Config", str(d))
    matches, method = reg.resolve("main config")
    assert method == "exact"
    assert len(matches) == 1


def test_resolve_substring(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d1 = tmp_path / "s1"
    d1.mkdir()
    d2 = tmp_path / "s2"
    d2.mkdir()
    reg.add("Main Config Dev", str(d1))
    reg.add("Test Server", str(d2))
    matches, method = reg.resolve("config")
    assert method == "substring"
    assert len(matches) == 1
    assert matches[0]["name"] == "Main Config Dev"


def test_resolve_substring_multiple(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d1 = tmp_path / "s1"
    d1.mkdir()
    d2 = tmp_path / "s2"
    d2.mkdir()
    reg.add("Config Alpha", str(d1))
    reg.add("Config Beta", str(d2))
    matches, method = reg.resolve("config")
    assert method == "substring"
    assert len(matches) == 2


def test_resolve_fuzzy(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    # "Alpah" -- distance 2 from "Alpha" (len 5, threshold min(1.5,3)=1 -- too strict)
    # Use longer name for threshold to kick in
    reg.remove("Alpha")
    reg.add("Main Config", str(d))
    # "Main Confgi" -- distance 2 from "Main Config" (len 11, threshold min(3.3,3)=3)
    matches, method = reg.resolve("Main Confgi")
    assert method == "fuzzy"
    assert len(matches) == 1


def test_resolve_none(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    matches, method = reg.resolve("Completely Different Name")
    assert method == "none"
    assert matches == []


def test_resolve_empty_query(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    matches, method = reg.resolve("")
    assert method == "none"
    assert matches == []


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_spaces(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    entry = reg.add("  Main   Config  ", str(d))
    assert entry["name"] == "Main Config"


# ---------------------------------------------------------------------------
# is_path_registered
# ---------------------------------------------------------------------------


def test_is_path_registered(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    assert reg.is_path_registered(str(d)) is True
    assert reg.is_path_registered(str(tmp_path / "other")) is False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persistence_across_instances(tmp_path):
    fp = tmp_path / "projects.json"
    d = tmp_path / "src"
    d.mkdir()

    reg1 = ProjectRegistry(fp)
    reg1.add("Alpha", str(d))

    reg2 = ProjectRegistry(fp)  # new instance, reads from disk
    projects = reg2.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "Alpha"


# ---------------------------------------------------------------------------
# Corrupted JSON
# ---------------------------------------------------------------------------


def test_corrupted_json_raises(tmp_path):
    fp = tmp_path / "projects.json"
    fp.write_text("{invalid json", encoding="utf-8")
    reg = ProjectRegistry(fp)
    with pytest.raises(RegistryCorruptedError, match="Cannot parse"):
        reg.list_projects()


def test_invalid_structure_array(tmp_path):
    """Valid JSON but wrong structure (array instead of object)."""
    fp = tmp_path / "projects.json"
    fp.write_text("[]", encoding="utf-8")
    reg = ProjectRegistry(fp)
    with pytest.raises(RegistryCorruptedError, match="Invalid structure"):
        reg.list_projects()


def test_invalid_structure_no_projects_key(tmp_path):
    """Valid JSON object but missing 'projects' key."""
    fp = tmp_path / "projects.json"
    fp.write_text('{"version": 1}', encoding="utf-8")
    reg = ProjectRegistry(fp)
    with pytest.raises(RegistryCorruptedError, match="Invalid structure"):
        reg.list_projects()


def test_corrupted_json_not_overwritten(tmp_path):
    fp = tmp_path / "projects.json"
    fp.write_text("{bad", encoding="utf-8")
    reg = ProjectRegistry(fp)
    with pytest.raises(RegistryCorruptedError):
        reg.list_projects()
    # File must NOT be overwritten
    assert fp.read_text(encoding="utf-8") == "{bad"


# ---------------------------------------------------------------------------
# Atomic write / .bak
# ---------------------------------------------------------------------------


def test_bak_created_on_save(tmp_path):
    fp = tmp_path / "projects.json"
    d = tmp_path / "src"
    d.mkdir()

    reg = ProjectRegistry(fp)
    reg.add("Alpha", str(d))
    assert fp.is_file()

    # Second write should create .bak
    d2 = tmp_path / "s2"
    d2.mkdir()
    reg.add("Beta", str(d2))
    bak = fp.with_suffix(".bak")
    assert bak.is_file()
    # .bak contains old data (only Alpha)
    data = json.loads(bak.read_text(encoding="utf-8"))
    assert len(data["projects"]) == 1
    assert data["projects"][0]["name"] == "Alpha"


# ---------------------------------------------------------------------------
# get_registry / _reset_registry singleton
# ---------------------------------------------------------------------------


def test_get_registry_with_path(tmp_path):
    """get_registry(path=...) returns a fresh instance, not the singleton."""
    fp = tmp_path / "projects.json"
    reg = get_registry(fp)
    assert reg.list_projects() == []


def test_get_registry_default_and_reset(tmp_path):
    """Singleton behaviour + reset."""
    _reset_registry()
    with patch.dict(os.environ, {"RLM_CONFIG_FILE": str(tmp_path / "service.json")}):
        _reset_registry()  # clear previous singleton
        reg1 = get_registry()
        reg2 = get_registry()
        assert reg1 is reg2  # same singleton
        _reset_registry()
        reg3 = get_registry()
        assert reg3 is not reg1  # new singleton after reset


def test_get_registry_respects_config_file(tmp_path):
    """RLM_CONFIG_FILE determines where projects.json lives."""
    cfg = tmp_path / "custom" / "service.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")

    _reset_registry()
    with patch.dict(os.environ, {"RLM_CONFIG_FILE": str(cfg)}):
        _reset_registry()
        reg = get_registry()
        assert reg._path == cfg.parent / "projects.json"


def test_get_registry_default_path(tmp_path):
    """Without RLM_CONFIG_FILE, uses SERVICE_JSON.parent."""
    _reset_registry()
    env = os.environ.copy()
    env.pop("RLM_CONFIG_FILE", None)
    with patch.dict(os.environ, env, clear=True):
        _reset_registry()
        reg = get_registry()
        from rlm_tools_bsl._config import SERVICE_JSON

        assert reg._path == SERVICE_JSON.parent / "projects.json"


# ---------------------------------------------------------------------------
# Password -- CRUD + sanitize
# ---------------------------------------------------------------------------


def test_add_with_password_stores_hash_not_plaintext(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    entry = reg.add("Alpha", str(d), password="secret123")
    # Returned entry must NOT contain password fields
    assert "password_hash" not in entry
    assert "password_salt" not in entry
    # But raw file must contain them
    raw = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    stored = raw["projects"][0]
    assert "password_hash" in stored
    assert "password_salt" in stored
    assert stored["password_hash"] != "secret123"  # not plaintext


def test_add_without_password_no_hash_fields(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    raw = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    stored = raw["projects"][0]
    assert "password_hash" not in stored
    assert "password_salt" not in stored


def test_add_empty_password_raises(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    with pytest.raises(ValueError, match="must not be empty"):
        reg.add("Alpha", str(d), password="")


def test_update_password(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    updated = reg.update("Alpha", password="newpass")
    assert "password_hash" not in updated
    assert reg.has_password("Alpha") is True
    assert reg.verify_password("Alpha", "newpass") is True


def test_update_clear_password(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    assert reg.has_password("Alpha") is True
    reg.update("Alpha", clear_password=True)
    assert reg.has_password("Alpha") is False


def test_update_password_and_clear_raises(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    with pytest.raises(ValueError, match="Cannot set password and clear_password"):
        reg.update("Alpha", password="x", clear_password=True)


def test_verify_password_correct(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="mypass")
    assert reg.verify_password("Alpha", "mypass") is True


def test_verify_password_wrong(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="mypass")
    assert reg.verify_password("Alpha", "wrong") is False


def test_verify_password_no_hash_returns_false(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    assert reg.verify_password("Alpha", "anything") is False


def test_has_password_true_false(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    reg.add("Beta", str(d))
    assert reg.has_password("Alpha") is True
    assert reg.has_password("Beta") is False


def test_list_projects_no_password_fields(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    projects = reg.list_projects()
    for p in projects:
        assert "password_hash" not in p
        assert "password_salt" not in p


def test_add_response_no_password_fields(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    entry = reg.add("Alpha", str(d), password="secret")
    assert "password_hash" not in entry
    assert "password_salt" not in entry


def test_update_response_no_password_fields(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d))
    entry = reg.update("Alpha", password="secret")
    assert "password_hash" not in entry
    assert "password_salt" not in entry


def test_remove_response_no_password_fields(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    entry = reg.remove("Alpha")
    assert "password_hash" not in entry
    assert "password_salt" not in entry


def test_rename_response_no_password_fields(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    entry = reg.rename("Alpha", "Beta")
    assert "password_hash" not in entry
    assert "password_salt" not in entry


def test_resolve_no_password_fields(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    matches, _ = reg.resolve("Alpha")
    for m in matches:
        assert "password_hash" not in m
        assert "password_salt" not in m


# --- has_password flag in sanitized output ---


def test_sanitize_has_password_true(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    entry = reg.add("Alpha", str(d), password="secret")
    assert entry["has_password"] is True


def test_sanitize_has_password_false(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    entry = reg.add("Alpha", str(d))
    assert entry["has_password"] is False


def test_list_has_password_flag(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("WithPwd", str(d), password="secret")
    reg.add("NoPwd", str(d))
    projects = reg.list_projects()
    by_name = {p["name"]: p for p in projects}
    assert by_name["WithPwd"]["has_password"] is True
    assert by_name["NoPwd"]["has_password"] is False


def test_remove_response_has_password(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    entry = reg.remove("Alpha")
    assert entry["has_password"] is True


def test_update_clear_response_has_password_false(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    entry = reg.update("Alpha", clear_password=True)
    assert entry["has_password"] is False


def test_rename_response_has_password(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    entry = reg.rename("Alpha", "Beta")
    assert entry["has_password"] is True


def test_resolve_has_password_flag(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.json")
    d = tmp_path / "src"
    d.mkdir()
    reg.add("Alpha", str(d), password="secret")
    matches, _ = reg.resolve("Alpha")
    assert matches[0]["has_password"] is True
