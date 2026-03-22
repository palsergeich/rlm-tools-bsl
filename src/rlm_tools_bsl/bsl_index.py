"""BSL Method Index — SQLite-based pre-index of procedures, functions, and call graph.

Provides fast lookup of all methods across a 1C/BSL codebase without full file scans.
The index is stored on disk and supports incremental updates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from rlm_tools_bsl.bsl_knowledge import BSL_PATTERNS
from rlm_tools_bsl.cache import _paths_hash
from rlm_tools_bsl.format_detector import BslFileInfo, parse_bsl_path

logger = logging.getLogger(__name__)

BUILDER_VERSION = 5

# ---------------------------------------------------------------------------
# Regex patterns copied from bsl_helpers._parse_procedures / _strip_code_line
# (autonomous — no runtime import from bsl_helpers)
# ---------------------------------------------------------------------------
_PROC_DEF_RE = re.compile(BSL_PATTERNS["procedure_def"], re.IGNORECASE)
_PROC_END_RE = re.compile(BSL_PATTERNS["procedure_end"], re.IGNORECASE)
_STRING_LITERAL_RE = re.compile(r'"[^"\r\n]*"')

# Call-extraction patterns
_QUALIFIED_CALL_RE = re.compile(r"(\w+)\.(\w+)\s*\(")
_SIMPLE_CALL_RE = re.compile(r"(\w+)\s*\(")

# BSL keywords to exclude from call graph
_BSL_KEYWORDS: frozenset[str] = frozenset({
    # Russian
    "Если", "Тогда", "Иначе", "ИначеЕсли", "КонецЕсли",
    "Пока", "Для", "Каждого", "Цикл", "КонецЦикла",
    "Возврат", "Новый", "Тип", "ТипЗнч", "Знач", "Перем",
    "Попытка", "Исключение", "КонецПопытки", "Выполнить",
    "НЕ", "И", "ИЛИ",
    "Процедура", "Функция", "КонецПроцедуры", "КонецФункции",
    # English
    "If", "Then", "Else", "ElsIf", "EndIf",
    "While", "For", "Each", "Do", "EndDo",
    "Return", "New", "Type", "TypeOf", "Val", "Var",
    "Try", "Except", "EndTry", "Execute",
    "NOT", "AND", "OR",
    "Procedure", "Function", "EndProcedure", "EndFunction",
})

# Case-insensitive set for fast lookup
_BSL_KEYWORDS_LOWER: frozenset[str] = frozenset(k.lower() for k in _BSL_KEYWORDS)

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS modules (
    id INTEGER PRIMARY KEY,
    rel_path TEXT UNIQUE NOT NULL,
    category TEXT,
    object_name TEXT,
    module_type TEXT,
    form_name TEXT,
    is_form INTEGER DEFAULT 0,
    mtime REAL,
    size INTEGER
);

CREATE TABLE IF NOT EXISTS methods (
    id INTEGER PRIMARY KEY,
    module_id INTEGER NOT NULL REFERENCES modules(id),
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_export INTEGER DEFAULT 0,
    params TEXT,
    line INTEGER,
    end_line INTEGER,
    loc INTEGER
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    caller_id INTEGER NOT NULL REFERENCES methods(id),
    callee_name TEXT NOT NULL,
    line INTEGER
);

CREATE INDEX IF NOT EXISTS idx_mod_object ON modules(object_name);
CREATE INDEX IF NOT EXISTS idx_mod_category ON modules(category);
CREATE INDEX IF NOT EXISTS idx_meth_name ON methods(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_name COLLATE NOCASE);
-- idx_calls_caller removed: saves ~56MB on ERP, update uses callee-based cleanup instead

-- Level-2 metadata tables (optional, controlled by --no-metadata flag)
CREATE TABLE IF NOT EXISTS event_subscriptions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    synonym TEXT,
    event TEXT,
    handler_module TEXT,
    handler_procedure TEXT,
    source_types TEXT,
    source_count INTEGER,
    file TEXT
);
CREATE INDEX IF NOT EXISTS idx_es_name ON event_subscriptions(name);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    synonym TEXT,
    method_name TEXT,
    handler_module TEXT,
    handler_procedure TEXT,
    use INTEGER DEFAULT 1,
    predefined INTEGER DEFAULT 0,
    restart_count INTEGER DEFAULT 0,
    restart_interval INTEGER DEFAULT 0,
    file TEXT
);
CREATE INDEX IF NOT EXISTS idx_sj_name ON scheduled_jobs(name);

CREATE TABLE IF NOT EXISTS functional_options (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    synonym TEXT,
    location TEXT,
    content TEXT,
    file TEXT
);
CREATE INDEX IF NOT EXISTS idx_fo_name ON functional_options(name);

-- Level-3: role rights (normalized, one row per right)
CREATE TABLE IF NOT EXISTS role_rights (
    id INTEGER PRIMARY KEY,
    role_name TEXT NOT NULL,
    object_name TEXT NOT NULL,
    right_name TEXT NOT NULL,
    file TEXT
);
CREATE INDEX IF NOT EXISTS idx_rr_object ON role_rights(object_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_rr_role ON role_rights(role_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_rr_right ON role_rights(right_name);

-- Level-3: register movements (in-band, extracted during BSL processing)
CREATE TABLE IF NOT EXISTS register_movements (
    id INTEGER PRIMARY KEY,
    document_name TEXT NOT NULL,
    register_name TEXT NOT NULL,
    source TEXT DEFAULT 'code',
    file TEXT
);
CREATE INDEX IF NOT EXISTS idx_rm_document ON register_movements(document_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_rm_register ON register_movements(register_name COLLATE NOCASE);

-- Level-3: enum values
CREATE TABLE IF NOT EXISTS enum_values (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    synonym TEXT,
    values_json TEXT NOT NULL,
    source_file TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enum_name ON enum_values(name COLLATE NOCASE);

-- Level-3: subsystem content (normalized, one row per subsystem-object pair)
CREATE TABLE IF NOT EXISTS subsystem_content (
    id INTEGER PRIMARY KEY,
    subsystem_name TEXT NOT NULL,
    subsystem_synonym TEXT,
    object_ref TEXT NOT NULL,
    file TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sc_object ON subsystem_content(object_ref COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_sc_subsystem ON subsystem_content(subsystem_name COLLATE NOCASE);

-- Level-4: file navigation index (glob/tree/find_files acceleration)
CREATE TABLE IF NOT EXISTS file_paths (
    id INTEGER PRIMARY KEY,
    rel_path TEXT NOT NULL UNIQUE,
    extension TEXT NOT NULL,
    dir_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    depth INTEGER NOT NULL,
    size INTEGER,
    mtime REAL
);
CREATE INDEX IF NOT EXISTS idx_fp_ext ON file_paths(extension);
CREATE INDEX IF NOT EXISTS idx_fp_dir ON file_paths(dir_path);
CREATE INDEX IF NOT EXISTS idx_fp_filename ON file_paths(filename COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_fp_depth ON file_paths(depth);
"""


# ---------------------------------------------------------------------------
# IndexStatus enum
# ---------------------------------------------------------------------------
class IndexStatus(Enum):
    """Result of freshness check for an existing method index."""
    FRESH = "fresh"
    STALE = "stale"
    STALE_AGE = "stale_age"
    STALE_CONTENT = "stale_content"
    MISSING = "missing"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def get_index_dir(base_path: str) -> Path:
    """Return the directory for storing indexes.

    Respects RLM_INDEX_DIR env variable; defaults to ~/.cache/rlm-tools-bsl/.
    """
    env_dir = os.environ.get("RLM_INDEX_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".cache" / "rlm-tools-bsl"


def get_index_db_path(base_path: str) -> Path:
    """Return the full path to the method_index.db for a given base_path."""
    h = hashlib.md5(base_path.encode()).hexdigest()[:12]
    return get_index_dir(base_path) / h / "method_index.db"


# ---------------------------------------------------------------------------
# Freshness check
# ---------------------------------------------------------------------------
def _read_index_meta(db_path: Path) -> dict[str, str] | None:
    """Read index_meta table from SQLite. Returns None on any error."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        meta: dict[str, str] = {}
        for row in conn.execute("SELECT key, value FROM index_meta"):
            meta[row["key"]] = row["value"]
        return meta
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _check_age(meta: dict[str, str]) -> IndexStatus | None:
    """Return STALE_AGE if index exceeds max age, else None."""
    max_age_days = int(os.environ.get("RLM_INDEX_MAX_AGE_DAYS", "7"))
    built_at = meta.get("built_at")
    if built_at is not None:
        age_days = (time.time() - float(built_at)) / 86400
        if age_days > max_age_days:
            return IndexStatus.STALE_AGE
    return None


def _check_content_sample(db_path: Path, base_path: str) -> IndexStatus | None:
    """Sample random modules and compare mtime+size. Returns STALE_CONTENT or None."""
    sample_size = int(os.environ.get("RLM_INDEX_SAMPLE_SIZE", "5"))
    sample_threshold = int(os.environ.get("RLM_INDEX_SAMPLE_THRESHOLD", "30"))

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None

    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM modules").fetchone()
        total_modules = row["cnt"] if row else 0

        if total_modules < sample_threshold:
            return None

        rows = conn.execute(
            "SELECT rel_path, mtime, size FROM modules ORDER BY RANDOM() LIMIT ?",
            (sample_size,),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()

    if not rows:
        return None

    base = Path(base_path)

    def _stat_check(r: sqlite3.Row) -> bool:
        """Return True if mismatch detected."""
        full_path = base / r["rel_path"]
        try:
            st = full_path.stat()
            return abs(st.st_mtime - r["mtime"]) > 1.0 or st.st_size != r["size"]
        except OSError:
            return True

    if len(rows) > 1:
        from concurrent.futures import ThreadPoolExecutor as _TP
        with _TP(max_workers=min(5, len(rows))) as pool:
            results = list(pool.map(_stat_check, rows))
        mismatches = sum(results)
    else:
        mismatches = 1 if _stat_check(rows[0]) else 0

    if mismatches > max(1, len(rows) // 5):
        return IndexStatus.STALE_CONTENT
    return None


def check_index_usable(
    db_path: str | Path,
    base_path: str,
) -> IndexStatus:
    """Lightweight freshness check for rlm_start (no rglob needed).

    Checks:
      1. File exists
      2. Age: RLM_INDEX_MAX_AGE_DAYS (default 7)
      3. Content sampling: random mtime+size on a small sample (default 5),
         skipped if index is younger than RLM_INDEX_SKIP_SAMPLE_HOURS (default 24)

    Structural drift (files added/removed) is NOT checked here — use
    check_index_strict() or compare format_info.bsl_file_count with
    index_meta bsl_count separately.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return IndexStatus.MISSING

    meta = _read_index_meta(db_path)
    if meta is None:
        return IndexStatus.MISSING

    # --- Age check ---
    age_status = _check_age(meta)
    if age_status is not None:
        return age_status

    # --- Skip sampling for young indexes ---
    skip_hours = int(os.environ.get("RLM_INDEX_SKIP_SAMPLE_HOURS", "24"))
    built_at = meta.get("built_at")
    if built_at is not None:
        age_hours = (time.time() - float(built_at)) / 3600
        if age_hours < skip_hours:
            return IndexStatus.FRESH

    # --- Content sampling (parallel stat) ---
    content_status = _check_content_sample(db_path, base_path)
    if content_status is not None:
        return content_status

    return IndexStatus.FRESH


def check_index_strict(
    db_path: str | Path,
    current_bsl_count: int,
    current_paths_hash: str,
    base_path: str,
) -> IndexStatus:
    """Full freshness check for CLI ``index info`` (requires rglob data).

    Checks:
      1. File exists
      2. Structural match: bsl_count + paths_hash
      3. Age: RLM_INDEX_MAX_AGE_DAYS (default 7)
      4. Content sampling: random mtime+size checks on a sample of files
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return IndexStatus.MISSING

    meta = _read_index_meta(db_path)
    if meta is None:
        return IndexStatus.MISSING

    # --- Structural check ---
    stored_count = meta.get("bsl_count")
    stored_hash = meta.get("paths_hash")
    if stored_count is None or stored_hash is None:
        return IndexStatus.STALE

    if int(stored_count) != current_bsl_count or stored_hash != current_paths_hash:
        return IndexStatus.STALE

    # --- Age check ---
    age_status = _check_age(meta)
    if age_status is not None:
        return age_status

    # --- Content sampling ---
    content_status = _check_content_sample(db_path, base_path)
    if content_status is not None:
        return content_status

    return IndexStatus.FRESH


# Backward-compatible alias
check_index_freshness = check_index_strict


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------
def _strip_code_line(line: str) -> str:
    """Remove comments and string literals from a BSL code line."""
    ci = line.find("//")
    if ci >= 0:
        line = line[:ci]
    line = _STRING_LITERAL_RE.sub("", line)
    return line


def _parse_procedures_from_lines(lines: list[str]) -> list[dict]:
    """Parse procedure/function definitions from a list of lines.

    Returns list of dicts: {name, type, line, end_line, is_export, params, loc}.
    """
    procedures: list[dict] = []
    current: dict | None = None

    for line_idx, line in enumerate(lines):
        line_number = line_idx + 1  # 1-based

        if current is None:
            m = _PROC_DEF_RE.search(line)
            if m:
                proc_type = m.group(1)
                proc_name = m.group(2)
                params = m.group(3).strip() if m.group(3) else ""
                is_export = m.group(4) is not None and m.group(4).strip() != ""
                current = {
                    "name": proc_name,
                    "type": proc_type,
                    "line": line_number,
                    "is_export": is_export,
                    "end_line": None,
                    "params": params,
                }
        else:
            m_end = _PROC_END_RE.search(line)
            if m_end:
                current["end_line"] = line_number
                current["loc"] = current["end_line"] - current["line"] + 1
                procedures.append(current)
                current = None

    # Handle unclosed procedure at EOF
    if current is not None:
        current["end_line"] = len(lines)
        current["loc"] = current["end_line"] - current["line"] + 1
        procedures.append(current)

    return procedures


def _extract_calls_from_body(
    lines: list[str],
    start_line: int,
    end_line: int,
) -> list[tuple[str, int]]:
    """Extract call targets from method body lines.

    Args:
        lines: All lines of the file (0-indexed).
        start_line: 1-based start (definition line, skipped).
        end_line: 1-based end (EndProcedure line, skipped).

    Returns:
        List of (callee_name, line_number_1based).
    """
    calls: list[tuple[str, int]] = []

    # Iterate over body lines (skip definition and end lines)
    body_start = start_line  # 1-based def line — skip it
    body_end = min(end_line - 1, len(lines))  # skip EndProcedure line

    for line_idx in range(body_start, body_end):  # 0-based index = body_start .. body_end-1
        if line_idx >= len(lines):
            break
        raw_line = lines[line_idx]
        cleaned = _strip_code_line(raw_line)
        if not cleaned.strip():
            continue

        line_number = line_idx + 1  # 1-based

        seen_on_line: set[str] = set()

        # Qualified calls first: Module.Method(
        for qm in _QUALIFIED_CALL_RE.finditer(cleaned):
            module_part = qm.group(1)
            method_part = qm.group(2)
            if module_part.lower() in _BSL_KEYWORDS_LOWER:
                continue
            if method_part.lower() in _BSL_KEYWORDS_LOWER:
                continue
            callee = f"{module_part}.{method_part}"
            if callee not in seen_on_line:
                seen_on_line.add(callee)
                calls.append((callee, line_number))

        # Simple calls: FunctionName(
        for sm in _SIMPLE_CALL_RE.finditer(cleaned):
            func_name = sm.group(1)
            if func_name.lower() in _BSL_KEYWORDS_LOWER:
                continue
            # Skip if already captured as part of a qualified call on this line
            # (the simple regex also matches the method part of Module.Method)
            if func_name not in seen_on_line:
                # Check this isn't the method part of a qualified call
                start_pos = sm.start()
                if start_pos > 0 and cleaned[start_pos - 1] == ".":
                    continue
                seen_on_line.add(func_name)
                calls.append((func_name, line_number))

    return calls


# ---------------------------------------------------------------------------
# Configuration XML parsing (Level 1 metadata)
# ---------------------------------------------------------------------------
def _parse_configuration_meta(base_path: str) -> dict[str, str]:
    """Extract top-level config metadata from Configuration.xml (CF) or Configuration.mdo (EDT).

    Returns dict with keys: config_name, config_synonym, config_version,
    config_vendor, source_format, config_role.
    """
    import xml.etree.ElementTree as ET

    from rlm_tools_bsl.format_detector import SourceFormat, detect_format

    base = Path(base_path)
    fmt_info = detect_format(base_path)
    # detect_format returns FormatInfo; extract the primary_format enum value
    if hasattr(fmt_info, "primary_format"):
        fmt_str = fmt_info.primary_format.value
    elif isinstance(fmt_info, SourceFormat):
        fmt_str = fmt_info.value
    else:
        fmt_str = str(fmt_info)
    meta: dict[str, str] = {"source_format": fmt_str}

    # Store shallow bsl_file_count from detect_format (fast glob, not rglob)
    if hasattr(fmt_info, "bsl_file_count"):
        meta["shallow_bsl_count"] = str(fmt_info.bsl_file_count)

    # Store has_configuration_xml flag
    meta["has_configuration_xml"] = "1" if (base / "Configuration.xml").is_file() else "0"

    # Try CF format: Configuration.xml in root
    cf_xml = base / "Configuration.xml"
    mdo_xml = base / "Configuration" / "Configuration.mdo"

    ns_cf = {
        "md": "http://v8.1c.ru/8.3/MDClasses",
        "v8": "http://v8.1c.ru/8.1/data/core",
    }

    def _cf_text(props, tag: str) -> str:
        el = props.find(f"md:{tag}", ns_cf)
        return (el.text or "").strip() if el is not None else ""

    def _cf_synonym(props) -> str:
        syn_el = props.find("md:Synonym", ns_cf)
        if syn_el is None:
            return ""
        for item in syn_el.findall("v8:item", ns_cf):
            lang = item.find("v8:lang", ns_cf)
            content = item.find("v8:content", ns_cf)
            if lang is not None and content is not None and lang.text == "ru":
                return (content.text or "").strip()
        # Fallback to first item
        for item in syn_el.findall("v8:item", ns_cf):
            content = item.find("v8:content", ns_cf)
            if content is not None and content.text:
                return content.text.strip()
        return ""

    if cf_xml.is_file():
        try:
            tree = ET.parse(str(cf_xml))
            root = tree.getroot()
            # Find <Configuration><Properties>
            cfg_el = root.find("md:Configuration", ns_cf)
            if cfg_el is not None:
                props = cfg_el.find("md:Properties", ns_cf)
                if props is not None:
                    meta["config_name"] = _cf_text(props, "Name")
                    meta["config_synonym"] = _cf_synonym(props)
                    meta["config_version"] = _cf_text(props, "Version")
                    meta["config_vendor"] = _cf_text(props, "Vendor")
                    ext_el = props.find("md:ConfigurationExtensionPurpose", ns_cf)
                    if ext_el is not None and ext_el.text:
                        meta["config_role"] = "extension"
                        meta["extension_purpose"] = ext_el.text.strip()
                    else:
                        meta["config_role"] = "base"
                    meta["extension_prefix"] = _cf_text(props, "NamePrefix")
        except (ET.ParseError, OSError):
            pass
    elif mdo_xml.is_file():
        try:
            tree = ET.parse(str(mdo_xml))
            root = tree.getroot()

            def _mdo_text(tag: str) -> str:
                for ch in root:
                    local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
                    if local == tag and ch.text:
                        return ch.text.strip()
                return ""

            def _mdo_synonym() -> str:
                for ch in root:
                    local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
                    if local == "synonym":
                        # Look for <v8:item> children
                        for item in ch:
                            item_local = item.tag.split("}")[-1] if "}" in item.tag else item.tag
                            if item_local == "item":
                                lang_el = None
                                content_el = None
                                for sub in item:
                                    sl = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                                    if sl == "lang":
                                        lang_el = sub
                                    elif sl == "content":
                                        content_el = sub
                                if lang_el is not None and content_el is not None:
                                    if lang_el.text == "ru":
                                        return (content_el.text or "").strip()
                        # Fallback: text content directly
                        if ch.text and ch.text.strip():
                            return ch.text.strip()
                return ""

            meta["config_name"] = _mdo_text("name")
            meta["config_synonym"] = _mdo_synonym()
            meta["config_version"] = _mdo_text("version")
            meta["config_vendor"] = _mdo_text("vendor")
            ext = _mdo_text("configurationExtensionPurpose")
            if ext:
                meta["config_role"] = "extension"
                meta["extension_purpose"] = ext
            else:
                meta["config_role"] = "base"
            meta["extension_prefix"] = _mdo_text("namePrefix")
        except (ET.ParseError, OSError):
            pass

    return meta


# ---------------------------------------------------------------------------
# Level-2 metadata collection (ES, SJ, FO)
# ---------------------------------------------------------------------------
def _collect_metadata_tables(base_path: str) -> dict[str, list[tuple]]:
    """Scan and parse EventSubscriptions, ScheduledJobs, FunctionalOptions XMLs.

    Returns dict with keys: event_subscriptions, scheduled_jobs, functional_options.
    Each value is a list of tuples ready for INSERT.
    """
    from rlm_tools_bsl.bsl_xml_parsers import (
        parse_enum_xml,
        parse_event_subscription_xml,
        parse_functional_option_xml,
        parse_metadata_xml,
        parse_scheduled_job_xml,
    )

    base = Path(base_path)
    result: dict[str, list[tuple]] = {
        "event_subscriptions": [],
        "scheduled_jobs": [],
        "functional_options": [],
        "enum_values": [],
        "subsystem_content": [],
    }

    def _read(fp: Path) -> str | None:
        try:
            return fp.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return None

    def _glob_xml(category: str) -> list[Path]:
        files: list[Path] = []
        cat_dir = base / category
        if not cat_dir.is_dir():
            return files
        for fp in cat_dir.rglob("*"):
            if fp.suffix.lower() in (".xml", ".mdo") and fp.is_file():
                files.append(fp)
        return files

    # EventSubscriptions
    for fp in _glob_xml("EventSubscriptions"):
        content = _read(fp)
        if not content:
            continue
        parsed = parse_event_subscription_xml(content)
        if not parsed or not parsed.get("name"):
            continue
        handler = parsed.get("handler") or ""
        parts = handler.rsplit(".", 1)
        handler_module = parts[0].replace("CommonModule.", "") if len(parts) > 1 else ""
        handler_procedure = parts[-1] if parts else ""
        source_types = parsed.get("source_types") or []
        rel = fp.relative_to(base).as_posix()
        result["event_subscriptions"].append((
            parsed["name"],
            parsed.get("synonym") or "",
            parsed.get("event") or "",
            handler_module,
            handler_procedure,
            json.dumps(source_types, ensure_ascii=False),
            len(source_types),
            rel,
        ))

    # ScheduledJobs
    for fp in _glob_xml("ScheduledJobs"):
        content = _read(fp)
        if not content:
            continue
        parsed = parse_scheduled_job_xml(content)
        if not parsed or not parsed.get("name"):
            continue
        method_name = parsed.get("method_name") or ""
        parts = method_name.rsplit(".", 1)
        handler_module = parts[0].replace("CommonModule.", "") if len(parts) > 1 else ""
        handler_procedure = parts[-1] if parts else ""
        restart = parsed.get("restart_on_failure") or {}
        rel = fp.relative_to(base).as_posix()
        result["scheduled_jobs"].append((
            parsed["name"],
            parsed.get("synonym") or "",
            method_name,
            handler_module,
            handler_procedure,
            1 if parsed.get("use", True) else 0,
            1 if parsed.get("predefined", False) else 0,
            restart.get("count", 0),
            restart.get("interval", 0),
            rel,
        ))

    # FunctionalOptions
    for fp in _glob_xml("FunctionalOptions"):
        content = _read(fp)
        if not content:
            continue
        parsed = parse_functional_option_xml(content)
        if not parsed or not parsed.get("name"):
            continue
        fo_content = parsed.get("content") or []
        rel = fp.relative_to(base).as_posix()
        result["functional_options"].append((
            parsed["name"],
            parsed.get("synonym") or "",
            parsed.get("location") or "",
            json.dumps(fo_content, ensure_ascii=False),
            rel,
        ))

    # Enums
    for fp in _glob_xml("Enums"):
        content = _read(fp)
        if not content:
            continue
        parsed = parse_enum_xml(content)
        if not parsed or not parsed.get("name"):
            continue
        rel = fp.relative_to(base).as_posix()
        result["enum_values"].append((
            parsed["name"],
            parsed.get("synonym") or "",
            json.dumps(parsed.get("values", []), ensure_ascii=False),
            rel,
        ))

    # Subsystems
    for fp in _glob_xml("Subsystems"):
        content = _read(fp)
        if not content:
            continue
        parsed = parse_metadata_xml(content)
        if not parsed or parsed.get("object_type") != "Subsystem":
            continue
        sub_name = parsed.get("name", "")
        sub_synonym = parsed.get("synonym", "")
        sub_content = parsed.get("content", [])
        rel = fp.relative_to(base).as_posix()
        for obj_ref in sub_content:
            result["subsystem_content"].append((
                sub_name,
                sub_synonym,
                obj_ref,
                rel,
            ))

    return result


def _insert_metadata_tables(conn: sqlite3.Connection, tables: dict[str, list[tuple]]) -> None:
    """Insert Level-2 metadata into the database."""
    # Clear existing data
    conn.execute("DELETE FROM event_subscriptions")
    conn.execute("DELETE FROM scheduled_jobs")
    conn.execute("DELETE FROM functional_options")
    try:
        conn.execute("DELETE FROM enum_values")
    except sqlite3.OperationalError:
        pass

    if tables["event_subscriptions"]:
        conn.executemany(
            "INSERT INTO event_subscriptions "
            "(name, synonym, event, handler_module, handler_procedure, source_types, source_count, file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            tables["event_subscriptions"],
        )

    if tables["scheduled_jobs"]:
        conn.executemany(
            "INSERT INTO scheduled_jobs "
            "(name, synonym, method_name, handler_module, handler_procedure, "
            "use, predefined, restart_count, restart_interval, file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tables["scheduled_jobs"],
        )

    if tables["functional_options"]:
        conn.executemany(
            "INSERT INTO functional_options "
            "(name, synonym, location, content, file) "
            "VALUES (?, ?, ?, ?, ?)",
            tables["functional_options"],
        )

    if tables.get("enum_values"):
        conn.executemany(
            "INSERT INTO enum_values "
            "(name, synonym, values_json, source_file) "
            "VALUES (?, ?, ?, ?)",
            tables["enum_values"],
        )

    try:
        conn.execute("DELETE FROM subsystem_content")
    except sqlite3.OperationalError:
        pass
    if tables.get("subsystem_content"):
        conn.executemany(
            "INSERT INTO subsystem_content "
            "(subsystem_name, subsystem_synonym, object_ref, file) "
            "VALUES (?, ?, ?, ?)",
            tables["subsystem_content"],
        )


# ---------------------------------------------------------------------------
# File paths collection for navigation index
# ---------------------------------------------------------------------------
_FILE_NAV_EXTENSIONS = {".bsl", ".mdo", ".xml"}

# Directories to skip (same as helpers._SKIP_DIRS)
_SKIP_DIRS_NAV = {
    ".git", ".build", "node_modules", ".venv", "venv",
    "__pycache__", ".tox", ".mypy_cache", ".cache", ".rlm_cache",
}


def _collect_file_paths(base_path: str) -> list[tuple]:
    """Collect all .bsl/.mdo/.xml file paths for the navigation index.

    Returns list of tuples ready for INSERT:
        (rel_path, extension, dir_path, filename, depth, size, mtime)
    """
    base = Path(base_path)
    rows: list[tuple] = []

    for dirpath, dirnames, filenames in os.walk(base):
        # Filter out hidden/skip directories
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS_NAV and not d.startswith(".")
        ]
        for fname in filenames:
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _FILE_NAV_EXTENSIONS:
                continue

            full_path = Path(dirpath) / fname
            try:
                st = full_path.stat()
            except OSError:
                continue

            rel = full_path.relative_to(base).as_posix()
            parts = rel.split("/")
            dir_path = "/".join(parts[:-1]) if len(parts) > 1 else ""
            depth = len(parts)

            rows.append((rel, ext, dir_path, fname, depth, st.st_size, st.st_mtime))

    return rows


def _insert_file_paths(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """Insert file navigation paths into the database."""
    try:
        conn.execute("DELETE FROM file_paths")
    except sqlite3.OperationalError:
        pass
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO file_paths "
            "(rel_path, extension, dir_path, filename, depth, size, mtime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


# ---------------------------------------------------------------------------
# Regex for register movements (in-band extraction from Document BSL)
# ---------------------------------------------------------------------------
_MOVEMENTS_RE = re.compile(r'\u0414\u0432\u0438\u0436\u0435\u043d\u0438\u044f\.(\w+)')  # Движения.RegName
_ERP_MECHANISM_RE = re.compile(
    r'\u041c\u0435\u0445\u0430\u043d\u0438\u0437\u043c\u044b\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430'
    r'\.\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c\(\s*"(\w+)"'
)  # МеханизмыДокумента.Добавить("RegName")
_MANAGER_TABLE_RE = re.compile(
    r'(?:\u0424\u0443\u043d\u043a\u0446\u0438\u044f|\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430)\s+'
    r'\u0422\u0435\u043a\u0441\u0442\u0417\u0430\u043f\u0440\u043e\u0441\u0430'
    r'\u0422\u0430\u0431\u043b\u0438\u0446\u0430(\w+)\s*\(',
    re.IGNORECASE,
)  # Функция|Процедура ТекстЗапросаТаблицаRegName(
_ADAPTED_PROC_RE = re.compile(
    r'(?:\u0424\u0443\u043d\u043a\u0446\u0438\u044f|\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430)\s+'
    r'\u0410\u0434\u0430\u043f\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439'
    r'\u0422\u0435\u043a\u0441\u0442\u0417\u0430\u043f\u0440\u043e\u0441\u0430'
    r'\u0414\u0432\u0438\u0436\u0435\u043d\u0438\u0439\u041f\u043e\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u0443\b.*?\n'
    r'(.*?)'
    r'\n\s*\u041a\u043e\u043d\u0435\u0446(?:\u0424\u0443\u043d\u043a\u0446\u0438\u0438|\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u044b)',
    re.IGNORECASE | re.DOTALL,
)  # Функция|Процедура АдаптированныйТекстЗапросаДвиженийПоРегистру...КонецФункции|КонецПроцедуры
_ADAPTED_REG_RE = re.compile(
    r'\u0418\u043c\u044f\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u0430\s*=\s*"(\w+)"',
    re.IGNORECASE,
)  # ИмяРегистра = "RegName"


class FileResult(NamedTuple):
    """Result of processing a single .bsl file."""
    info: BslFileInfo
    mtime: float
    size: int
    methods: list[dict]
    raw_calls: list[tuple[int, str, int]]
    movements: list[tuple[str, str, str]]  # (register_name, source, rel_path)


def _extract_movements(
    content: str, info: BslFileInfo, rel_path: str,
) -> list[tuple[str, str, str]]:
    """Extract register movements from Document modules (in-band, no extra I/O)."""
    if info.category != "Documents":
        return []
    if info.module_type not in ("ObjectModule", "ManagerModule"):
        return []

    results: list[tuple[str, str, str]] = []

    if info.module_type == "ObjectModule":
        for m in _MOVEMENTS_RE.finditer(content):
            results.append((m.group(1), "code", rel_path))
    elif info.module_type == "ManagerModule":
        for m in _ERP_MECHANISM_RE.finditer(content):
            results.append((m.group(1), "erp_mechanism", rel_path))
        for m in _MANAGER_TABLE_RE.finditer(content):
            results.append((m.group(1), "manager_table", rel_path))
        adapted_match = _ADAPTED_PROC_RE.search(content)
        if adapted_match:
            for m in _ADAPTED_REG_RE.finditer(adapted_match.group(1)):
                results.append((m.group(1), "adapted", rel_path))

    return results


def _process_single_file(
    file_path: Path,
    base_path: str,
    build_calls: bool,
) -> FileResult | None:
    """Process a single .bsl file: parse metadata, methods, optionally calls and movements.

    Returns:
        FileResult namedtuple or None on error.
    """
    try:
        st = file_path.stat()
        mtime = st.st_mtime
        size = st.st_size
    except OSError:
        return None

    info = parse_bsl_path(str(file_path), base_path)

    try:
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
    except OSError:
        return None

    lines = content.splitlines()
    methods = _parse_procedures_from_lines(lines)

    raw_calls: list[tuple[int, str, int]] = []
    if build_calls:
        for method_idx, method in enumerate(methods):
            start = method["line"]
            end = method["end_line"] if method["end_line"] else len(lines)
            for callee_name, call_line in _extract_calls_from_body(lines, start, end):
                raw_calls.append((method_idx, callee_name, call_line))

    # In-band: extract register movements from Document modules (no extra I/O)
    rel_path = info.relative_path
    movements = _extract_movements(content, info, rel_path)

    return FileResult(info, mtime, size, methods, raw_calls, movements)


# ---------------------------------------------------------------------------
# Regex-based role rights parsing (4x faster than ElementTree)
# ---------------------------------------------------------------------------
# Both CF (Rights.xml) and EDT (.rights) use the same XML format:
#   <object>
#     <name>Category.ObjectName</name>
#     <right><name>Read</name><value>true</value></right>
#   </object>
def _parse_role_rights_for_index(
    content: str, role_name: str, file_path: str,
) -> list[tuple[str, str, str, str]]:
    """Parse role rights using ElementTree. Returns list of (role_name, object_name, right_name, file)."""
    from rlm_tools_bsl.bsl_xml_parsers import parse_rights_xml

    results: list[tuple[str, str, str, str]] = []
    for entry in parse_rights_xml(content):
        full_name = entry["object"]
        for right in entry["rights"]:
            results.append((role_name, full_name, right, file_path))
    return results


def _collect_role_rights(base_path: str) -> list[tuple[str, str, str, str]]:
    """Collect role rights from all Roles directories.

    Returns list of (role_name, object_name, right_name, file_path).
    """
    import glob as glob_mod

    base = Path(base_path)
    all_results: list[tuple[str, str, str, str]] = []

    # Find all rights files
    rights_files: list[tuple[str, Path]] = []

    # CF format: Roles/*/Ext/Rights.xml
    for f in base.glob("**/Roles/*/Ext/Rights.xml"):
        role_name = f.parent.parent.name
        rights_files.append((role_name, f))

    # EDT format: Roles/*/*.rights
    for f in base.glob("**/Roles/*/*.rights"):
        role_name = f.parent.name
        rights_files.append((role_name, f))

    def _process_rights_file(
        item: tuple[str, Path],
    ) -> list[tuple[str, str, str, str]]:
        role_name, f = item
        try:
            content = f.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return []
        rel = f.relative_to(base).as_posix()
        return _parse_role_rights_for_index(content, role_name, rel)

    if len(rights_files) > 1:
        workers = min(os.cpu_count() or 4, 8)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for batch in pool.map(_process_rights_file, rights_files):
                all_results.extend(batch)
    elif rights_files:
        all_results.extend(_process_rights_file(rights_files[0]))

    return all_results


# ---------------------------------------------------------------------------
# Prefix detection from index
# ---------------------------------------------------------------------------
_PREFIX_RE = re.compile(r'^([a-z\u0430-\u044f\u0451]+_?)')


def _detect_prefixes(conn: sqlite3.Connection) -> list[str]:
    """Detect custom prefixes from object_name in modules table.

    Uses the same heuristic as bsl_helpers._ensure_prefixes() but runs on
    already-indexed data (no I/O). Returns sorted list of frequent prefixes.
    """
    rows = conn.execute(
        "SELECT DISTINCT object_name FROM modules WHERE object_name IS NOT NULL"
    ).fetchall()

    prefix_counts: dict[str, int] = {}
    for row in rows:
        name = row[0]
        if not name or not name[0].islower():
            continue
        m = _PREFIX_RE.match(name)
        if m:
            key = m.group(1).rstrip("_").lower()
            if len(key) >= 2:
                prefix_counts[key] = prefix_counts.get(key, 0) + 1

    # Keep prefixes appearing 3+ times
    frequent = sorted(
        ((k, v) for k, v in prefix_counts.items() if v >= 3),
        key=lambda x: -x[1],
    )
    return [k for k, _ in frequent]


# ---------------------------------------------------------------------------
# IndexBuilder
# ---------------------------------------------------------------------------
class IndexBuilder:
    """Builds and incrementally updates the SQLite method index."""

    def build(
        self,
        base_path: str,
        build_calls: bool = True,
        build_metadata: bool = True,
        build_fts: bool = True,
    ) -> Path:
        """Full build of the method index.

        Scans all .bsl files under base_path, extracts methods and optionally
        a heuristic call graph, and writes results to a SQLite database.

        Args:
            base_path: Root directory of the 1C configuration.
            build_calls: Whether to build the call graph.
            build_metadata: Whether to parse Level-2 metadata (ES/SJ/FO).
            build_fts: Whether to build FTS5 full-text search index.

        Returns:
            Path to the created database file.
        """
        db_path = get_index_db_path(base_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove old DB if it exists
        if db_path.exists():
            db_path.unlink()

        logger.info("Building method index for %s -> %s", base_path, db_path)
        t0 = time.time()

        # Discover all .bsl files
        base = Path(base_path)
        bsl_files = sorted(base.rglob("*.bsl"))
        total_files = len(bsl_files)
        logger.info("Found %d .bsl files", total_files)

        if total_files == 0:
            # Create empty DB with schema + file_paths for .mdo/.xml
            conn = sqlite3.connect(str(db_path))
            conn.executescript(_SCHEMA_SQL)
            fp_rows = _collect_file_paths(base_path)
            _insert_file_paths(conn, fp_rows)
            self._write_meta(conn, base_path, 0, "", build_calls, build_metadata,
                             build_fts=build_fts, file_paths_count=len(fp_rows))
            conn.close()
            return db_path

        # Compute paths hash
        rel_paths = [
            Path(f).relative_to(base).as_posix() for f in bsl_files
        ]
        paths_hash = _paths_hash(rel_paths)

        # Parallel processing
        results: list[tuple[BslFileInfo, float, int, list[dict], list[tuple[int, str, int]]]] = []
        workers = min(os.cpu_count() or 4, 8)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_process_single_file, fp, base_path, build_calls): fp
                for fp in bsl_files
            }
            done_count = 0
            for future in as_completed(futures):
                done_count += 1
                if done_count % 1000 == 0:
                    elapsed = time.time() - t0
                    rate = done_count / elapsed if elapsed > 0 else 0
                    logger.info(
                        "Progress: %d/%d files (%.0f files/sec)",
                        done_count, total_files, rate,
                    )
                result = future.result()
                if result is not None:
                    results.append(result)

        # Write to SQLite
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA_SQL)

        self._bulk_insert(conn, results, build_calls)

        # Level-1 metadata: Configuration XML
        config_meta = _parse_configuration_meta(base_path)

        # Level-2 metadata: ES, SJ, FO
        if build_metadata:
            md_tables = _collect_metadata_tables(base_path)
            _insert_metadata_tables(conn, md_tables)

        # Level-3: register movements (in-band, already extracted)
        all_movements: list[tuple[str, str, str, str]] = []
        for r in results:
            if r.movements and r.info.object_name:
                for reg_name, source, file_path_str in r.movements:
                    all_movements.append((r.info.object_name, reg_name, source, file_path_str))
        if all_movements:
            conn.executemany(
                "INSERT INTO register_movements (document_name, register_name, source, file) "
                "VALUES (?, ?, ?, ?)",
                all_movements,
            )
            conn.commit()
            logger.info("Register movements: %d entries", len(all_movements))

        # Level-3: role rights (parallel regex parsing)
        role_rights = _collect_role_rights(base_path)
        if role_rights:
            conn.executemany(
                "INSERT INTO role_rights (role_name, object_name, right_name, file) "
                "VALUES (?, ?, ?, ?)",
                role_rights,
            )
            conn.commit()
            logger.info("Role rights: %d entries from %d roles",
                       len(role_rights), len(set(r[0] for r in role_rights)))

        # Level-4: file navigation index (.bsl/.mdo/.xml paths)
        file_paths_rows = _collect_file_paths(base_path)
        _insert_file_paths(conn, file_paths_rows)
        conn.commit()
        logger.info("File paths: %d entries", len(file_paths_rows))

        # FTS5 full-text search index for methods
        if build_fts:
            conn.execute(
                "CREATE VIRTUAL TABLE methods_fts USING fts5("
                "name, object_name, tokenize='trigram')"
            )
            conn.execute(
                "INSERT INTO methods_fts(rowid, name, object_name) "
                "SELECT m.id, m.name, mod.object_name "
                "FROM methods m JOIN modules mod ON mod.id = m.module_id"
            )

        # Detect custom prefixes from object names in index
        detected_prefixes = _detect_prefixes(conn)

        self._write_meta(
            conn, base_path, total_files, paths_hash,
            build_calls, build_metadata, config_meta, build_fts,
            detected_prefixes=detected_prefixes,
            file_paths_count=len(file_paths_rows),
        )

        conn.execute("ANALYZE")
        conn.execute("VACUUM")
        conn.close()

        elapsed = time.time() - t0
        total_methods = sum(len(r.methods) for r in results)
        total_calls = sum(len(r.raw_calls) for r in results)
        logger.info(
            "Index built: %d modules, %d methods, %d calls in %.1fs (%.0f files/sec)",
            len(results), total_methods, total_calls,
            elapsed, total_files / elapsed if elapsed > 0 else 0,
        )

        return db_path

    def update(self, base_path: str) -> dict:
        """Incremental update by mtime+size delta.

        Returns:
            dict with keys: added, changed, removed (counts).
        """
        db_path = get_index_db_path(base_path)
        if not db_path.exists():
            raise FileNotFoundError(f"Index not found: {db_path}")

        t0 = time.time()
        base = Path(base_path)

        # Current files on disk
        bsl_files = sorted(base.rglob("*.bsl"))
        disk_files: dict[str, Path] = {}
        for fp in bsl_files:
            rel = fp.relative_to(base).as_posix()
            disk_files[rel] = fp

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        # Read build settings from meta
        meta_row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'has_calls'"
        ).fetchone()
        build_calls = meta_row is not None and meta_row["value"] == "1"

        meta_row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'has_metadata'"
        ).fetchone()
        has_metadata = meta_row is not None and meta_row["value"] == "1"

        meta_row = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'has_fts'"
        ).fetchone()
        has_fts = meta_row is not None and meta_row["value"] == "1"

        # Existing modules in DB
        db_modules: dict[str, dict] = {}
        for row in conn.execute("SELECT id, rel_path, mtime, size FROM modules"):
            db_modules[row["rel_path"]] = {
                "id": row["id"],
                "mtime": row["mtime"],
                "size": row["size"],
            }

        # Compute delta
        disk_set = set(disk_files.keys())
        db_set = set(db_modules.keys())

        added_paths = disk_set - db_set
        removed_paths = db_set - disk_set
        common_paths = disk_set & db_set

        changed_paths: set[str] = set()
        for rel in common_paths:
            fp = disk_files[rel]
            try:
                st = fp.stat()
                db_info = db_modules[rel]
                if abs(st.st_mtime - db_info["mtime"]) > 1.0 or st.st_size != db_info["size"]:
                    changed_paths.add(rel)
            except OSError:
                changed_paths.add(rel)

        to_remove = removed_paths | changed_paths
        to_add = added_paths | changed_paths

        logger.info(
            "Incremental update: %d added, %d changed, %d removed",
            len(added_paths), len(changed_paths), len(removed_paths),
        )

        bsl_changed = bool(to_remove or to_add)

        # Process BSL delta (modules, methods, calls, movements)
        if bsl_changed:
            results: list[FileResult] = []
            if to_add:
                workers = min(os.cpu_count() or 4, 8)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            _process_single_file, disk_files[rel], base_path, build_calls,
                        ): rel
                        for rel in to_add
                        if rel in disk_files
                    }
                    for future in as_completed(futures):
                        result = future.result()
                        if result is not None:
                            results.append(result)

            with conn:
                # Delete old data for removed + changed
                if to_remove:
                    for rel in to_remove:
                        mod_info = db_modules.get(rel)
                        if mod_info is None:
                            continue
                        mod_id = mod_info["id"]
                        method_ids = [
                            row[0]
                            for row in conn.execute(
                                "SELECT id FROM methods WHERE module_id = ?", (mod_id,)
                            )
                        ]
                        if method_ids:
                            placeholders = ",".join("?" * len(method_ids))
                            conn.execute(
                                f"DELETE FROM calls WHERE caller_id IN ({placeholders})",
                                method_ids,
                            )
                            if has_fts:
                                conn.execute(
                                    f"DELETE FROM methods_fts WHERE rowid IN ({placeholders})",
                                    method_ids,
                                )
                        conn.execute("DELETE FROM methods WHERE module_id = ?", (mod_id,))
                        conn.execute("DELETE FROM modules WHERE id = ?", (mod_id,))

                # Insert new data
                self._bulk_insert(conn, results, build_calls)

                # Update FTS for newly inserted methods
                if has_fts and results:
                    new_rel_paths = [r.info.relative_path for r in results]
                    placeholders = ",".join("?" * len(new_rel_paths))
                    conn.execute(
                        f"INSERT INTO methods_fts(rowid, name, object_name) "
                        f"SELECT m.id, m.name, mod.object_name "
                        f"FROM methods m JOIN modules mod ON mod.id = m.module_id "
                        f"WHERE mod.rel_path IN ({placeholders})",
                        new_rel_paths,
                    )

                # Update register_movements for changed/added Document modules
                if results:
                    changed_doc_names = set()
                    for r in results:
                        if r.info.category == "Documents" and r.info.object_name:
                            changed_doc_names.add(r.info.object_name)
                    for doc_name in changed_doc_names:
                        try:
                            conn.execute(
                                "DELETE FROM register_movements WHERE document_name = ?",
                                (doc_name,),
                            )
                        except sqlite3.OperationalError:
                            pass
                    new_movements: list[tuple[str, str, str, str]] = []
                    for r in results:
                        if r.movements and r.info.object_name:
                            for reg_name, source, fpath in r.movements:
                                new_movements.append((r.info.object_name, reg_name, source, fpath))
                    if new_movements:
                        conn.executemany(
                            "INSERT INTO register_movements "
                            "(document_name, register_name, source, file) VALUES (?, ?, ?, ?)",
                            new_movements,
                        )

                # Update meta
                new_paths_hash = _paths_hash(sorted(disk_files.keys()))
                conn.execute(
                    "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                    ("bsl_count", str(len(disk_files))),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                    ("paths_hash", new_paths_hash),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                    ("built_at", str(time.time())),
                )

        # Refresh Level-1 metadata (config version may have changed)
        config_meta = _parse_configuration_meta(base_path)
        for key, value in config_meta.items():
            conn.execute(
                "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

        # Refresh Level-2 metadata if originally built with metadata
        if has_metadata:
            # Ensure tables exist (in case of schema upgrade)
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS event_subscriptions ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, synonym TEXT, "
                "event TEXT, handler_module TEXT, handler_procedure TEXT, "
                "source_types TEXT, source_count INTEGER, file TEXT);\n"
                "CREATE TABLE IF NOT EXISTS scheduled_jobs ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, synonym TEXT, "
                "method_name TEXT, handler_module TEXT, handler_procedure TEXT, "
                "use INTEGER DEFAULT 1, predefined INTEGER DEFAULT 0, "
                "restart_count INTEGER DEFAULT 0, restart_interval INTEGER DEFAULT 0, file TEXT);\n"
                "CREATE TABLE IF NOT EXISTS functional_options ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, synonym TEXT, "
                "location TEXT, content TEXT, file TEXT);\n"
                "CREATE TABLE IF NOT EXISTS enum_values ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, synonym TEXT, "
                "values_json TEXT NOT NULL, source_file TEXT NOT NULL);\n"
                "CREATE INDEX IF NOT EXISTS idx_enum_name ON enum_values(name COLLATE NOCASE);\n"
                "CREATE TABLE IF NOT EXISTS subsystem_content ("
                "id INTEGER PRIMARY KEY, subsystem_name TEXT NOT NULL, "
                "subsystem_synonym TEXT, object_ref TEXT NOT NULL, file TEXT NOT NULL);\n"
                "CREATE INDEX IF NOT EXISTS idx_sc_object ON subsystem_content(object_ref COLLATE NOCASE);\n"
                "CREATE INDEX IF NOT EXISTS idx_sc_subsystem ON subsystem_content(subsystem_name COLLATE NOCASE);\n"
            )
            md_tables = _collect_metadata_tables(base_path)
            _insert_metadata_tables(conn, md_tables)

        # Refresh role_rights (full rebuild — cheap, ~346K entries in ~2s)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS role_rights ("
            "id INTEGER PRIMARY KEY, role_name TEXT NOT NULL, "
            "object_name TEXT NOT NULL, right_name TEXT NOT NULL, file TEXT);\n"
            "CREATE INDEX IF NOT EXISTS idx_rr_object ON role_rights(object_name COLLATE NOCASE);\n"
        )
        conn.execute("DELETE FROM role_rights")
        role_rights = _collect_role_rights(base_path)
        if role_rights:
            conn.executemany(
                "INSERT INTO role_rights (role_name, object_name, right_name, file) "
                "VALUES (?, ?, ?, ?)",
                role_rights,
            )

        # Refresh file_paths (full rebuild — cheap for 30-50K files)
        file_paths_rows = _collect_file_paths(base_path)
        # Ensure table exists (schema upgrade v4→v5)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS file_paths ("
            "id INTEGER PRIMARY KEY, rel_path TEXT NOT NULL UNIQUE, "
            "extension TEXT NOT NULL, dir_path TEXT NOT NULL, "
            "filename TEXT NOT NULL, depth INTEGER NOT NULL, "
            "size INTEGER, mtime REAL);\n"
            "CREATE INDEX IF NOT EXISTS idx_fp_ext ON file_paths(extension);\n"
            "CREATE INDEX IF NOT EXISTS idx_fp_dir ON file_paths(dir_path);\n"
            "CREATE INDEX IF NOT EXISTS idx_fp_filename ON file_paths(filename COLLATE NOCASE);\n"
            "CREATE INDEX IF NOT EXISTS idx_fp_depth ON file_paths(depth);\n"
        )
        _insert_file_paths(conn, file_paths_rows)
        conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
            ("file_paths_count", str(len(file_paths_rows))),
        )

        conn.commit()
        conn.execute("ANALYZE")
        conn.close()

        elapsed = time.time() - t0
        logger.info("Incremental update done in %.1fs", elapsed)

        return {
            "added": len(added_paths),
            "changed": len(changed_paths),
            "removed": len(removed_paths),
        }

    # --- Private helpers ---

    @staticmethod
    def _bulk_insert(
        conn: sqlite3.Connection,
        results: list[FileResult],
        build_calls: bool,
    ) -> None:
        """Insert modules, methods, and calls in batch."""
        module_rows: list[tuple] = []
        for r in results:
            module_rows.append((
                r.info.relative_path,
                r.info.category,
                r.info.object_name,
                r.info.module_type,
                r.info.form_name,
                1 if r.info.is_form_module else 0,
                r.mtime,
                r.size,
            ))

        conn.executemany(
            "INSERT OR REPLACE INTO modules "
            "(rel_path, category, object_name, module_type, form_name, is_form, mtime, size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            module_rows,
        )

        # Build rel_path -> module_id map
        path_to_id: dict[str, int] = {}
        for row in conn.execute("SELECT id, rel_path FROM modules"):
            path_to_id[row[0] if isinstance(row, tuple) else row["rel_path"]] = (
                row[1] if isinstance(row, tuple) else row["id"]
            )
        # Fix: sqlite3 without row_factory returns tuples (id, rel_path)
        path_to_id_fixed: dict[str, int] = {}
        for row in conn.execute("SELECT id, rel_path FROM modules"):
            if isinstance(row, sqlite3.Row):
                path_to_id_fixed[row["rel_path"]] = row["id"]
            else:
                path_to_id_fixed[row[1]] = row[0]
        path_to_id = path_to_id_fixed

        # Insert methods and collect method IDs for calls
        method_rows: list[tuple] = []
        # We need to track method insertions to map method_idx -> method_id for calls
        call_pending: list[tuple[str, int, str, int]] = []  # (rel_path, method_idx, callee, line)

        for r in results:
            mod_id = path_to_id.get(r.info.relative_path)
            if mod_id is None:
                continue
            for method in r.methods:
                method_rows.append((
                    mod_id,
                    method["name"],
                    method["type"],
                    1 if method["is_export"] else 0,
                    method["params"],
                    method["line"],
                    method["end_line"],
                    method.get("loc"),
                ))
            if build_calls:
                for method_idx, callee_name, call_line in r.raw_calls:
                    call_pending.append((r.info.relative_path, method_idx, callee_name, call_line))

        conn.executemany(
            "INSERT OR REPLACE INTO methods "
            "(module_id, name, type, is_export, params, line, end_line, loc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            method_rows,
        )

        # Insert calls — need to resolve method_idx to method_id
        if build_calls and call_pending:
            # Build (rel_path) -> sorted list of method IDs by line
            methods_by_module: dict[int, list[int]] = {}
            for row in conn.execute("SELECT id, module_id FROM methods ORDER BY module_id, line"):
                if isinstance(row, sqlite3.Row):
                    mid, modid = row["id"], row["module_id"]
                else:
                    mid, modid = row[0], row[1]
                methods_by_module.setdefault(modid, []).append(mid)

            call_rows: list[tuple] = []
            for r in results:
                mod_id = path_to_id.get(r.info.relative_path)
                if mod_id is None:
                    continue
                method_ids = methods_by_module.get(mod_id, [])

                for method_idx, callee_name, call_line in r.raw_calls:
                    if method_idx < len(method_ids):
                        caller_method_id = method_ids[method_idx]
                        call_rows.append((caller_method_id, callee_name, call_line))

            if call_rows:
                conn.executemany(
                    "INSERT INTO calls (caller_id, callee_name, line) VALUES (?, ?, ?)",
                    call_rows,
                )

    @staticmethod
    def _write_meta(
        conn: sqlite3.Connection,
        base_path: str,
        bsl_count: int,
        paths_hash: str,
        build_calls: bool,
        build_metadata: bool = False,
        config_meta: dict[str, str] | None = None,
        build_fts: bool = False,
        detected_prefixes: list[str] | None = None,
        file_paths_count: int = 0,
    ) -> None:
        """Write index metadata."""
        meta_entries = [
            ("version", str(BUILDER_VERSION)),
            ("bsl_count", str(bsl_count)),
            ("paths_hash", paths_hash),
            ("built_at", str(time.time())),
            ("builder_version", str(BUILDER_VERSION)),
            ("base_path", base_path),
            ("has_calls", "1" if build_calls else "0"),
            ("has_metadata", "1" if build_metadata else "0"),
            ("has_fts", "1" if build_fts else "0"),
            ("file_paths_count", str(file_paths_count)),
        ]
        # Level-1: Configuration metadata
        if config_meta:
            for key, value in config_meta.items():
                meta_entries.append((key, value))

        # Detected custom prefixes
        if detected_prefixes:
            meta_entries.append(("detected_prefixes", json.dumps(
                detected_prefixes, ensure_ascii=False,
            )))

        conn.executemany(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
            meta_entries,
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Glob pattern dispatcher (whitelist-based, not universal translator)
# ---------------------------------------------------------------------------
def _can_index_glob(pattern: str) -> tuple[str, dict] | None:
    """Check if a glob pattern can be served from the file_paths index.

    Returns (strategy_name, params) or None for FS fallback.

    Supported patterns:
      **/*.ext            → ('by_extension', {ext: '.ext'})
      **/Dir/**/*.ext     → ('under_prefix_ext', {dir_name: 'Dir', ext: '.ext'})
      Dir/**/*.ext        → ('prefix_recursive_ext', {prefix: 'Dir', ext: '.ext'})
      Dir/*/File.ext      → ('dir_file', {dir: 'Dir', file: 'File.ext'})
      Dir/** or Dir/**/*  → ('under_prefix', {prefix: 'Dir'})
      exact/path          → ('exact', {path: 'exact/path'})
      **/Name.*           → ('name_wildcard', {name_prefix: 'Name', ext: ''})
      **/Name.ext         → ('name_wildcard', {name_prefix: 'Name', ext: '.ext'})
    """
    if not pattern:
        return None

    # Normalize to POSIX
    pattern = pattern.replace("\\", "/")

    # **/*.ext — all files with given extension
    if pattern.startswith("**/") and pattern.count("/") == 1:
        rest = pattern[3:]  # after **/
        if "*" not in rest and "?" not in rest:
            # **/Name.ext — specific file by name
            if "." in rest:
                name, ext = rest.rsplit(".", 1)
                return ("name_wildcard", {"name_prefix": name, "ext": "." + ext})
            return None
        if rest.startswith("*.") and "*" not in rest[2:] and "?" not in rest[2:]:
            ext = "." + rest[2:]
            return ("by_extension", {"ext": ext})
        if rest == "*":
            # **/* — all files (too broad, let FS handle)
            return None
        # **/Name.* — name wildcard
        if rest.endswith(".*") and "*" not in rest[:-2] and "?" not in rest[:-2]:
            name_prefix = rest[:-2]
            return ("name_wildcard", {"name_prefix": name_prefix, "ext": ""})
        return None

    # Dir/**/*.ext — recursive under prefix (anchored), filter by extension
    m = re.match(r"^([^*?]+)/\*\*/\*(\.[^/*?]+)$", pattern)
    if m:
        return ("prefix_recursive_ext", {"prefix": m.group(1), "ext": m.group(2)})

    # **/Dir/**/*.ext — recursive under directory name, filter by extension
    m = re.match(r"^\*\*/([^/*?]+)/\*\*/\*(\.[^/*?]+)$", pattern)
    if m:
        return ("under_prefix_ext", {"dir_name": m.group(1), "ext": m.group(2)})

    # Dir/** or Dir/**/*
    if pattern.endswith("/**") or pattern.endswith("/**/*"):
        prefix = pattern.split("/**")[0]
        if "*" not in prefix and "?" not in prefix:
            return ("under_prefix", {"prefix": prefix})
        return None

    # Dir/*/File.ext — single-level wildcard
    parts = pattern.split("/")
    if len(parts) == 3 and parts[1] == "*" and "*" not in parts[0] and "*" not in parts[2]:
        return ("dir_file", {"dir": parts[0], "file": parts[2]})

    # No wildcards — exact path
    if "*" not in pattern and "?" not in pattern:
        return ("exact", {"path": pattern})

    # Everything else → fallback to FS
    return None


# ---------------------------------------------------------------------------
# IndexReader (read-only)
# ---------------------------------------------------------------------------
class IndexReader:
    """Read-only interface to the method index database.

    Thread-safe: uses a per-instance lock for all database operations.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._lock = __import__("threading").Lock()
        # Open in read-only mode via URI
        self._conn = sqlite3.connect(
            f"file:{self._db_path}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

    @property
    def has_calls(self) -> bool:
        """Check if the calls table has any data."""
        with self._lock:
            try:
                row = self._conn.execute("SELECT COUNT(*) AS cnt FROM calls").fetchone()
                return row is not None and row["cnt"] > 0
            except sqlite3.Error:
                return False

    def get_methods_by_path(self, rel_path: str) -> list[dict] | None:
        """Get all methods for a given module path.

        Returns:
            List of dicts {name, type, line, end_line, is_export, params} or None
            if the module is not in the index.
        """
        with self._lock:
            mod_row = self._conn.execute(
                "SELECT id FROM modules WHERE rel_path = ?", (rel_path,)
            ).fetchone()
            if mod_row is None:
                return None

            rows = self._conn.execute(
                "SELECT name, type, line, end_line, is_export, params "
                "FROM methods WHERE module_id = ? ORDER BY line",
                (mod_row["id"],),
            ).fetchall()

            return [
                {
                    "name": r["name"],
                    "type": r["type"],
                    "line": r["line"],
                    "end_line": r["end_line"],
                    "is_export": bool(r["is_export"]),
                    "params": r["params"],
                }
                for r in rows
            ]

    def get_callers(
        self,
        proc_name: str,
        module_hint: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict | None:
        """Find callers of a procedure/function using the call graph index.

        Returns a dict matching the find_callers_context format:
        {
            "callers": [{file, caller_name, caller_is_export, line, object_name,
                         category, module_type}],
            "_meta": {total_callers, returned, offset, has_more}
        }
        Returns None if the calls table has no data.
        """
        with self._lock:
            try:
                count_row = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM calls"
                ).fetchone()
                if count_row is None or count_row["cnt"] == 0:
                    return None
            except sqlite3.Error:
                return None

            # Build query: match callee_name case-insensitively
            # Also match qualified calls (e.g., "Module.Method" matches "Method")
            query = """
                SELECT
                    c.line AS call_line,
                    c.callee_name,
                    m.name AS caller_name,
                    m.is_export AS caller_is_export,
                    mod.rel_path,
                    mod.object_name,
                    mod.category,
                    mod.module_type
                FROM calls c
                JOIN methods m ON m.id = c.caller_id
                JOIN modules mod ON mod.id = m.module_id
                WHERE c.callee_name LIKE ? ESCAPE '\\'
            """
            params: list = [proc_name]  # exact match first

            # Try exact match (case-insensitive via COLLATE on index)
            # Also search for qualified variants: *.proc_name
            escaped_name = proc_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

            # We want: callee_name = proc_name OR callee_name LIKE '%.proc_name'
            query = """
                SELECT
                    c.line AS call_line,
                    c.callee_name,
                    m.name AS caller_name,
                    m.is_export AS caller_is_export,
                    mod.rel_path,
                    mod.object_name,
                    mod.category,
                    mod.module_type
                FROM calls c
                JOIN methods m ON m.id = c.caller_id
                JOIN modules mod ON mod.id = m.module_id
                WHERE (c.callee_name = ? COLLATE NOCASE
                       OR c.callee_name LIKE ? ESCAPE '\\')
            """
            params_list: list = [proc_name, f"%.{escaped_name}"]

            if module_hint:
                query += " AND mod.object_name LIKE ? COLLATE NOCASE"
                params_list.append(f"%{module_hint}%")

            # Count total
            _t0 = time.monotonic()
            if not module_hint:
                # Fast path: COUNT on calls table only (uses idx_calls_callee)
                count_query = (
                    "SELECT COUNT(*) AS cnt FROM calls "
                    "WHERE (callee_name = ? COLLATE NOCASE "
                    "       OR callee_name LIKE ? ESCAPE '\\')"
                )
                count_params = [proc_name, f"%.{escaped_name}"]
                count_row = self._conn.execute(count_query, count_params).fetchone()
            else:
                # Exact path: COUNT via JOIN (precise, with module filter)
                count_query = f"SELECT COUNT(*) AS cnt FROM ({query})"
                count_row = self._conn.execute(count_query, params_list).fetchone()
            _t_count = time.monotonic() - _t0
            total_callers = count_row["cnt"] if count_row else 0

            # Fetch page
            query += " ORDER BY mod.rel_path, call_line LIMIT ? OFFSET ?"
            params_list.extend([limit, offset])

            _t0 = time.monotonic()
            rows = self._conn.execute(query, params_list).fetchall()
            _t_rows = time.monotonic() - _t0

            logger.debug(
                "get_callers: proc=%s count_time=%.2fs rows_time=%.2fs total=%d returned=%d",
                proc_name, _t_count, _t_rows, total_callers, len(rows),
            )

            callers = [
                {
                    "file": r["rel_path"],
                    "caller_name": r["caller_name"],
                    "caller_is_export": bool(r["caller_is_export"]),
                    "line": r["call_line"],
                    "object_name": r["object_name"],
                    "category": r["category"],
                    "module_type": r["module_type"],
                }
                for r in rows
            ]

            return {
                "callers": callers,
                "_meta": {
                    "total_callers": total_callers,
                    "returned": len(callers),
                    "offset": offset,
                    "has_more": (offset + limit) < total_callers,
                },
            }

    def get_exports_by_path(self, rel_path: str) -> list[dict] | None:
        """Get exported methods for a given module path.

        Returns:
            List of dicts {name, type, line, end_line, params} or None
            if the module is not in the index.
        """
        with self._lock:
            mod_row = self._conn.execute(
                "SELECT id FROM modules WHERE rel_path = ?", (rel_path,)
            ).fetchone()
            if mod_row is None:
                return None

            rows = self._conn.execute(
                "SELECT name, type, line, end_line, params "
                "FROM methods WHERE module_id = ? AND is_export = 1 ORDER BY line",
                (mod_row["id"],),
            ).fetchall()

            return [
                {
                    "name": r["name"],
                    "type": r["type"],
                    "line": r["line"],
                    "end_line": r["end_line"],
                    "params": r["params"],
                }
                for r in rows
            ]

    def get_register_movements(self, document_name: str) -> list[dict] | None:
        """Get register movements for a given document.

        Returns list of {register_name, source, file} or None if table empty/missing.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT DISTINCT register_name, source, file "
                    "FROM register_movements WHERE document_name = ? COLLATE NOCASE",
                    (document_name,),
                ).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                try:
                    cnt = self._conn.execute(
                        "SELECT COUNT(*) AS cnt FROM register_movements"
                    ).fetchone()
                    if cnt and cnt["cnt"] == 0:
                        return None
                except sqlite3.Error:
                    return None

            return [
                {"register_name": r["register_name"], "source": r["source"], "file": r["file"]}
                for r in rows
            ]

    def get_register_writers(self, register_name: str) -> list[dict] | None:
        """Get documents that write to a given register.

        Returns list of {document_name, source, file} or None if table empty/missing.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT document_name, source, file "
                    "FROM register_movements WHERE register_name = ? COLLATE NOCASE",
                    (register_name,),
                ).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                try:
                    cnt = self._conn.execute(
                        "SELECT COUNT(*) AS cnt FROM register_movements"
                    ).fetchone()
                    if cnt and cnt["cnt"] == 0:
                        return None
                except sqlite3.Error:
                    return None

            return [
                {"document_name": r["document_name"], "source": r["source"], "file": r["file"]}
                for r in rows
            ]

    def get_roles(self, object_name: str) -> list[dict] | None:
        """Get roles that grant rights to a given object.

        Returns list of {role_name, object_name, right_name, file} or None
        if role_rights table is empty/missing.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT role_name, object_name, right_name, file "
                    "FROM role_rights WHERE object_name LIKE ?",
                    (f"%{object_name}%",),
                ).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                # Check if the table has any data at all
                try:
                    cnt = self._conn.execute(
                        "SELECT COUNT(*) AS cnt FROM role_rights"
                    ).fetchone()
                    if cnt and cnt["cnt"] == 0:
                        return None
                except sqlite3.Error:
                    return None

            # Group by role_name, deduplicate rights
            role_map: dict[str, dict] = {}
            for r in rows:
                key = r["role_name"]
                if key not in role_map:
                    role_map[key] = {
                        "role_name": r["role_name"],
                        "object": r["object_name"],
                        "rights": [],
                        "file": r["file"],
                    }
                right = r["right_name"]
                if right not in role_map[key]["rights"]:
                    role_map[key]["rights"].append(right)
            return list(role_map.values())

    def get_enum_values(self, enum_name: str) -> dict | None:
        """Get enum definition from the index.

        Args:
            enum_name: Enum name (or fragment, case-insensitive).

        Returns:
            Dict with name, synonym, values, file — or None if table missing / not found.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT name, synonym, values_json, source_file FROM enum_values"
                ).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                return None

            name_lower = enum_name.lower()
            for r in rows:
                if name_lower in r["name"].lower():
                    values = []
                    try:
                        values = json.loads(r["values_json"]) if r["values_json"] else []
                    except (ValueError, TypeError):
                        pass
                    return {
                        "name": r["name"],
                        "synonym": r["synonym"] or "",
                        "values": values,
                        "file": r["source_file"] or "",
                    }

            # Table exists but enum not found — return error, don't fallback
            return {"error": f"Перечисление '{enum_name}' не найдено"}

    def get_startup_meta(self) -> dict | None:
        """Get cached startup metadata for fast rlm_start.

        Returns dict with source_format, shallow_bsl_count, config_role,
        config_name, extension_prefix, extension_purpose, has_configuration_xml —
        or None if required keys are missing.
        """
        with self._lock:
            meta: dict[str, str | None] = {}
            required_keys = ("source_format", "shallow_bsl_count")
            for key in ("source_format", "shallow_bsl_count", "config_role",
                         "config_name", "extension_prefix", "extension_purpose",
                         "has_configuration_xml"):
                row = self._conn.execute(
                    "SELECT value FROM index_meta WHERE key = ?", (key,)
                ).fetchone()
                meta[key] = row["value"] if row else None

            # Required keys must be present
            if any(meta.get(k) is None for k in required_keys):
                return None

            return meta

    def get_detected_prefixes(self) -> list[str]:
        """Return detected custom prefixes from index_meta, or empty list."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM index_meta WHERE key = 'detected_prefixes'"
            ).fetchone()
            if row and row["value"]:
                try:
                    return json.loads(row["value"])
                except (json.JSONDecodeError, TypeError):
                    return []
            return []

    def get_all_modules(self) -> list[dict]:
        """Return all modules from the index for fast _index_state init.

        Returns:
            list of dicts {rel_path, category, object_name, module_type, form_name}.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT rel_path, category, object_name, module_type, form_name "
                "FROM modules"
            ).fetchall()
            return [
                {
                    "rel_path": r["rel_path"],
                    "category": r["category"],
                    "object_name": r["object_name"],
                    "module_type": r["module_type"],
                    "form_name": r["form_name"],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # File navigation (Level-4: file_paths table)
    # ------------------------------------------------------------------
    @property
    def has_file_paths(self) -> bool:
        """Check if file_paths table exists and has data."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM file_paths"
                ).fetchone()
                return row is not None and row["cnt"] > 0
            except sqlite3.OperationalError:
                return False

    def glob_files(self, pattern: str) -> list[str] | None:
        """Resolve a glob pattern from the index.

        Returns sorted list of POSIX-relative paths, or None if the pattern
        is not supported (caller should fall back to FS).
        """
        strategy = _can_index_glob(pattern)
        if strategy is None:
            return None
        kind, params = strategy
        with self._lock:
            try:
                if kind == "by_extension":
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths WHERE extension = ? "
                        "ORDER BY rel_path",
                        (params["ext"],),
                    ).fetchall()
                elif kind == "under_prefix":
                    prefix = params["prefix"]
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths WHERE rel_path LIKE ? "
                        "ORDER BY rel_path",
                        (prefix + "/%",),
                    ).fetchall()
                elif kind == "dir_file":
                    dir_pat = params["dir"]
                    fname = params["file"]
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths "
                        "WHERE dir_path LIKE ? AND filename = ? "
                        "ORDER BY rel_path",
                        (dir_pat + "/%", fname),
                    ).fetchall()
                elif kind == "exact":
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths WHERE rel_path = ?",
                        (params["path"],),
                    ).fetchall()
                elif kind == "prefix_recursive_ext":
                    prefix = params["prefix"]
                    ext = params["ext"]
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths "
                        "WHERE rel_path LIKE ? AND extension = ? "
                        "ORDER BY rel_path",
                        (prefix + "/%", ext),
                    ).fetchall()
                elif kind == "under_prefix_ext":
                    dir_name = params["dir_name"]
                    ext = params["ext"]
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths "
                        "WHERE dir_path LIKE ? AND extension = ? "
                        "ORDER BY rel_path",
                        (f"%/{dir_name}/%", ext),
                    ).fetchall()
                elif kind == "name_wildcard":
                    name_prefix = params["name_prefix"]
                    ext = params.get("ext", "")
                    if ext:
                        rows = self._conn.execute(
                            "SELECT rel_path FROM file_paths "
                            "WHERE filename LIKE ? AND extension = ? "
                            "ORDER BY rel_path",
                            (name_prefix + "%", ext),
                        ).fetchall()
                    else:
                        rows = self._conn.execute(
                            "SELECT rel_path FROM file_paths "
                            "WHERE filename LIKE ? "
                            "ORDER BY rel_path",
                            (name_prefix + "%",),
                        ).fetchall()
                else:
                    return None
            except sqlite3.OperationalError:
                return None

        return [r["rel_path"] for r in rows]

    def tree_paths(self, prefix: str, max_depth: int) -> list[str] | None:
        """Get file paths for tree rendering from the index.

        Args:
            prefix: Directory prefix (POSIX), empty string for root.
            max_depth: Maximum depth relative to prefix.

        Returns sorted list of POSIX-relative paths, or None if table missing.
        """
        with self._lock:
            try:
                if prefix and prefix != ".":
                    # Normalize prefix
                    prefix = prefix.replace("\\", "/").strip("/")
                    base_depth = prefix.count("/") + 1
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths "
                        "WHERE rel_path LIKE ? AND depth <= ? "
                        "ORDER BY rel_path",
                        (prefix + "/%", base_depth + max_depth),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT rel_path FROM file_paths "
                        "WHERE depth <= ? "
                        "ORDER BY rel_path",
                        (max_depth,),
                    ).fetchall()
            except sqlite3.OperationalError:
                return None

        return [r["rel_path"] for r in rows]

    def find_files_indexed(self, name: str, limit: int = 100) -> list[str] | None:
        """Find files by substring match using the index.

        Ranking: exact filename > prefix filename > substring filename > substring path.

        Returns sorted list of POSIX-relative paths, or None if table missing.
        """
        if not name:
            return None
        with self._lock:
            try:
                # NOTE: SQLite LOWER() only works for ASCII.
                # For Unicode (Cyrillic) we match case-sensitively in SQL
                # then do Python-side case-insensitive ranking.
                needle_sql = "%" + name + "%"
                rows = self._conn.execute(
                    "SELECT rel_path, filename "
                    "FROM file_paths "
                    "WHERE filename LIKE ? OR rel_path LIKE ? "
                    "ORDER BY length(rel_path), rel_path "
                    "LIMIT ?",
                    (needle_sql, needle_sql, limit * 3),
                ).fetchall()
            except sqlite3.OperationalError:
                return None

        # Python-side ranking: exact filename > prefix > substring filename > substring path
        needle_lower = name.lower()
        ranked: list[tuple[int, str]] = []
        for r in rows:
            fn = r["filename"].lower()
            rp = r["rel_path"].lower()
            if fn == needle_lower:
                rank = 0
            elif fn.startswith(needle_lower):
                rank = 1
            elif needle_lower in fn:
                rank = 2
            elif needle_lower in rp:
                rank = 3
            else:
                continue
            ranked.append((rank, r["rel_path"]))
        ranked.sort(key=lambda x: (x[0], len(x[1]), x[1]))
        return [rp for _, rp in ranked[:limit]]

    def get_statistics(self) -> dict:
        """Get summary statistics about the index.

        Returns:
            dict with keys: modules, methods, calls, exports, built_at,
            config_name, config_version, source_format, has_metadata,
            event_subscriptions, scheduled_jobs, functional_options.
        """
        with self._lock:
            stats: dict = {}

            row = self._conn.execute("SELECT COUNT(*) AS cnt FROM modules").fetchone()
            stats["modules"] = row["cnt"] if row else 0

            row = self._conn.execute("SELECT COUNT(*) AS cnt FROM methods").fetchone()
            stats["methods"] = row["cnt"] if row else 0

            try:
                row = self._conn.execute("SELECT COUNT(*) AS cnt FROM calls").fetchone()
                stats["calls"] = row["cnt"] if row else 0
            except sqlite3.Error:
                stats["calls"] = 0

            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM methods WHERE is_export = 1"
            ).fetchone()
            stats["exports"] = row["cnt"] if row else 0

            # built_at from meta
            meta_row = self._conn.execute(
                "SELECT value FROM index_meta WHERE key = 'built_at'"
            ).fetchone()
            stats["built_at"] = float(meta_row["value"]) if meta_row else None

            # Configuration metadata from index_meta
            for key in ("config_name", "config_version", "config_synonym",
                        "config_vendor", "source_format", "config_role",
                        "has_metadata", "has_fts", "bsl_count"):
                meta_row = self._conn.execute(
                    "SELECT value FROM index_meta WHERE key = ?", (key,)
                ).fetchone()
                stats[key] = meta_row["value"] if meta_row else None

            # Convert stringly-typed flags to proper booleans
            for flag in ("has_fts", "has_metadata"):
                stats[flag] = stats.get(flag) == "1"

            # Convert bsl_count to int
            if stats.get("bsl_count") is not None:
                stats["bsl_count"] = int(stats["bsl_count"])

            # Level-2 metadata counts
            for table in ("event_subscriptions", "scheduled_jobs", "functional_options"):
                try:
                    row = self._conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()  # noqa: S608
                    stats[table] = row["cnt"] if row else 0
                except sqlite3.Error:
                    stats[table] = 0

            # Level-3 counts
            for table in ("role_rights", "register_movements", "enum_values", "subsystem_content"):
                try:
                    row = self._conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()  # noqa: S608
                    stats[table] = row["cnt"] if row else 0
                except sqlite3.Error:
                    stats[table] = 0

            # Level-4: file navigation
            try:
                row = self._conn.execute("SELECT COUNT(*) AS cnt FROM file_paths").fetchone()
                stats["file_paths"] = row["cnt"] if row else 0
            except sqlite3.Error:
                stats["file_paths"] = 0

            return stats

    @property
    def has_fts(self) -> bool:
        """Check if the FTS5 full-text search index exists."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM methods_fts"
                ).fetchone()
                return row is not None and row["cnt"] > 0
            except sqlite3.OperationalError:
                return False

    def search_methods(self, query: str, limit: int = 30) -> list[dict]:
        """FTS5 full-text search for methods by substring.

        Uses trigram tokenizer for substring matching with BM25 ranking.

        Args:
            query: Search query (substring match).
            limit: Max results (default 30).

        Returns:
            List of dicts ordered by relevance. Empty list if FTS not built.
        """
        if not query or not query.strip():
            return []
        with self._lock:
            try:
                # Wrap query in quotes for trigram substring matching
                fts_query = '"' + query.replace('"', '""') + '"'
                rows = self._conn.execute(
                    "SELECT "
                    "  m.name, m.type, m.is_export, m.line, m.end_line, m.params, "
                    "  mod.rel_path AS module_path, mod.object_name, "
                    "  methods_fts.rank "
                    "FROM methods_fts "
                    "JOIN methods m ON m.id = methods_fts.rowid "
                    "JOIN modules mod ON mod.id = m.module_id "
                    "WHERE methods_fts MATCH ? "
                    "ORDER BY methods_fts.rank "
                    "LIMIT ?",
                    (fts_query, limit),
                ).fetchall()

                return [
                    {
                        "name": r["name"],
                        "type": r["type"],
                        "is_export": bool(r["is_export"]),
                        "line": r["line"],
                        "end_line": r["end_line"],
                        "params": r["params"],
                        "module_path": r["module_path"],
                        "object_name": r["object_name"],
                        "rank": r["rank"],
                    }
                    for r in rows
                ]
            except sqlite3.OperationalError:
                return []

    def get_event_subscriptions(
        self, object_name: str = "", custom_only: bool = False,
    ) -> list[dict] | None:
        """Get event subscriptions from the index, optionally filtered.

        Args:
            object_name: Filter by source type (case-insensitive substring).
            custom_only: Not applied here (requires prefix detection from helpers).

        Returns:
            List of dicts matching find_event_subscriptions format, or None
            if the table is empty / missing.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT name, synonym, event, handler_module, handler_procedure, "
                    "source_types, source_count, file FROM event_subscriptions"
                ).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                return None

            result: list[dict] = []
            name_lower = object_name.lower() if object_name else ""

            for r in rows:
                source_types: list[str] = []
                try:
                    source_types = json.loads(r["source_types"]) if r["source_types"] else []
                except (ValueError, TypeError):
                    pass

                handler_module = r["handler_module"] or ""
                handler_procedure = r["handler_procedure"] or ""
                handler = (
                    f"CommonModule.{handler_module}.{handler_procedure}"
                    if handler_module else handler_procedure
                )

                entry = {
                    "name": r["name"],
                    "synonym": r["synonym"] or "",
                    "source_types": source_types,
                    "source_count": r["source_count"] or 0,
                    "event": r["event"] or "",
                    "handler": handler,
                    "handler_module": handler_module,
                    "handler_procedure": handler_procedure,
                    "file": r["file"] or "",
                }

                if name_lower:
                    if not source_types:
                        result.append(entry)
                    elif any(name_lower in t.lower() for t in source_types):
                        result.append(entry)
                else:
                    stripped = {k: v for k, v in entry.items() if k != "source_types"}
                    result.append(stripped)

            return result

    def get_scheduled_jobs(self, name: str = "") -> list[dict] | None:
        """Get scheduled jobs from the index, optionally filtered by name.

        Returns:
            List of dicts matching find_scheduled_jobs format, or None
            if the table is empty / missing.
        """
        with self._lock:
            try:
                sql = (
                    "SELECT name, synonym, method_name, handler_module, "
                    "handler_procedure, use, predefined, restart_count, "
                    "restart_interval, file FROM scheduled_jobs"
                )
                params: tuple = ()
                if name:
                    sql += " WHERE name LIKE ? COLLATE NOCASE"
                    params = (f"%{name}%",)
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                return [] if name else None

            return [
                {
                    "name": r["name"],
                    "synonym": r["synonym"] or "",
                    "method_name": r["method_name"] or "",
                    "handler_module": r["handler_module"] or "",
                    "handler_procedure": r["handler_procedure"] or "",
                    "use": bool(r["use"]),
                    "predefined": bool(r["predefined"]),
                    "restart_on_failure": {
                        "count": r["restart_count"] or 0,
                        "interval": r["restart_interval"] or 0,
                    },
                    "file": r["file"] or "",
                }
                for r in rows
            ]

    def get_functional_options(self, object_name: str = "") -> list[dict] | None:
        """Get functional options from the index, optionally filtered.

        Args:
            object_name: Filter by content list (case-insensitive substring).

        Returns:
            List of dicts matching find_functional_options format, or None
            if the table is empty / missing.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT name, synonym, location, content, file "
                    "FROM functional_options"
                ).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                return None

            result: list[dict] = []
            name_lower = object_name.lower() if object_name else ""

            for r in rows:
                content_list: list[str] = []
                try:
                    content_list = json.loads(r["content"]) if r["content"] else []
                except (ValueError, TypeError):
                    pass

                entry = {
                    "name": r["name"],
                    "synonym": r["synonym"] or "",
                    "location": r["location"] or "",
                    "content": content_list,
                    "file": r["file"] or "",
                }

                if name_lower:
                    if any(name_lower in c.lower() for c in content_list):
                        result.append(entry)
                else:
                    result.append(entry)

            return result

    def get_subsystems_for_object(self, object_name: str) -> list[dict] | None:
        """Find subsystems containing a given object.

        Args:
            object_name: Object name (case-insensitive substring match against object_ref).

        Returns:
            List of dicts {name, synonym, file, matched_refs} or None if table missing.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT subsystem_name, subsystem_synonym, object_ref, file "
                    "FROM subsystem_content WHERE object_ref LIKE ? COLLATE NOCASE",
                    (f"%{object_name}%",),
                ).fetchall()
            except sqlite3.OperationalError:
                return None

            if not rows:
                return []  # Table exists but no matches — don't fallback

            # Group by subsystem
            from collections import defaultdict

            grouped: dict[str, dict] = {}
            for r in rows:
                key = r["subsystem_name"]
                if key not in grouped:
                    grouped[key] = {"synonym": "", "file": "", "matched_refs": []}
                grouped[key]["synonym"] = r["subsystem_synonym"] or ""
                grouped[key]["file"] = r["file"] or ""
                grouped[key]["matched_refs"].append(r["object_ref"])

            return [
                {
                    "name": name,
                    "synonym": info["synonym"],
                    "file": info["file"],
                    "matched_refs": info["matched_refs"],
                }
                for name, info in grouped.items()
            ]

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
