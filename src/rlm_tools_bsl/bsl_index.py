"""BSL Method Index — SQLite-based pre-index of procedures, functions, and call graph.

Provides fast lookup of all methods across a 1C/BSL codebase without full file scans.
The index is stored on disk and supports incremental updates.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from pathlib import Path

from rlm_tools_bsl.bsl_knowledge import BSL_PATTERNS
from rlm_tools_bsl.cache import _paths_hash
from rlm_tools_bsl.format_detector import BslFileInfo, parse_bsl_path

logger = logging.getLogger(__name__)

BUILDER_VERSION = 2

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
def check_index_freshness(
    db_path: str | Path,
    current_bsl_count: int,
    current_paths_hash: str,
    base_path: str,
) -> IndexStatus:
    """Check whether an existing index is still valid.

    Checks:
      1. File exists
      2. Structural match: bsl_count + paths_hash
      3. Age: RLM_INDEX_MAX_AGE_DAYS (default 7)
      4. Content sampling: random mtime+size checks on a sample of files
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return IndexStatus.MISSING

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return IndexStatus.MISSING

    try:
        cur = conn.cursor()
        meta: dict[str, str] = {}
        try:
            for row in cur.execute("SELECT key, value FROM index_meta"):
                meta[row["key"]] = row["value"]
        except sqlite3.Error:
            return IndexStatus.MISSING

        # --- Structural check ---
        stored_count = meta.get("bsl_count")
        stored_hash = meta.get("paths_hash")
        if stored_count is None or stored_hash is None:
            return IndexStatus.STALE

        if int(stored_count) != current_bsl_count or stored_hash != current_paths_hash:
            return IndexStatus.STALE

        # --- Age check ---
        max_age_days = int(os.environ.get("RLM_INDEX_MAX_AGE_DAYS", "7"))
        built_at = meta.get("built_at")
        if built_at is not None:
            age_days = (time.time() - float(built_at)) / 86400
            if age_days > max_age_days:
                return IndexStatus.STALE_AGE

        # --- Content sampling (mtime + size) ---
        sample_size = int(os.environ.get("RLM_INDEX_SAMPLE_SIZE", "20"))
        sample_threshold = int(os.environ.get("RLM_INDEX_SAMPLE_THRESHOLD", "30"))

        # Only sample if there are enough modules
        total_modules = 0
        try:
            row = cur.execute("SELECT COUNT(*) AS cnt FROM modules").fetchone()
            total_modules = row["cnt"] if row else 0
        except sqlite3.Error:
            pass

        if total_modules >= sample_threshold:
            # Random sample from modules table
            rows = cur.execute(
                "SELECT rel_path, mtime, size FROM modules ORDER BY RANDOM() LIMIT ?",
                (sample_size,),
            ).fetchall()

            base = Path(base_path)
            mismatches = 0
            for row in rows:
                full_path = base / row["rel_path"]
                try:
                    st = full_path.stat()
                    if abs(st.st_mtime - row["mtime"]) > 1.0 or st.st_size != row["size"]:
                        mismatches += 1
                except OSError:
                    mismatches += 1

            # If more than 20% of sample mismatches, content is stale
            if mismatches > max(1, len(rows) // 5):
                return IndexStatus.STALE_CONTENT

        return IndexStatus.FRESH
    finally:
        conn.close()


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
                    else:
                        meta["config_role"] = "base"
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
            meta["config_role"] = "extension" if ext else "base"
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
    import json as _json

    from rlm_tools_bsl.bsl_xml_parsers import (
        parse_event_subscription_xml,
        parse_functional_option_xml,
        parse_scheduled_job_xml,
    )

    base = Path(base_path)
    result: dict[str, list[tuple]] = {
        "event_subscriptions": [],
        "scheduled_jobs": [],
        "functional_options": [],
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
            _json.dumps(source_types, ensure_ascii=False),
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
            _json.dumps(fo_content, ensure_ascii=False),
            rel,
        ))

    return result


def _insert_metadata_tables(conn: sqlite3.Connection, tables: dict[str, list[tuple]]) -> None:
    """Insert Level-2 metadata into the database."""
    # Clear existing data
    conn.execute("DELETE FROM event_subscriptions")
    conn.execute("DELETE FROM scheduled_jobs")
    conn.execute("DELETE FROM functional_options")

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


def _process_single_file(
    file_path: Path,
    base_path: str,
    build_calls: bool,
) -> tuple[BslFileInfo, float, int, list[dict], list[tuple[int, str, int]]] | None:
    """Process a single .bsl file: parse metadata, methods, and optionally calls.

    Returns:
        (info, mtime, size, methods, raw_calls) or None on error.
        raw_calls: list of (method_index_in_methods, callee_name, line).
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

    return info, mtime, size, methods, raw_calls


# ---------------------------------------------------------------------------
# IndexBuilder
# ---------------------------------------------------------------------------
class IndexBuilder:
    """Builds and incrementally updates the SQLite method index."""

    def build(self, base_path: str, build_calls: bool = True, build_metadata: bool = True) -> Path:
        """Full build of the method index.

        Scans all .bsl files under base_path, extracts methods and optionally
        a heuristic call graph, and writes results to a SQLite database.

        Args:
            base_path: Root directory of the 1C configuration.
            build_calls: Whether to build the call graph.
            build_metadata: Whether to parse Level-2 metadata (ES/SJ/FO).

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
            # Create empty DB with schema
            conn = sqlite3.connect(str(db_path))
            conn.executescript(_SCHEMA_SQL)
            self._write_meta(conn, base_path, 0, "", build_calls, build_metadata)
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

        self._write_meta(
            conn, base_path, total_files, paths_hash,
            build_calls, build_metadata, config_meta,
        )

        conn.execute("ANALYZE")
        conn.execute("VACUUM")
        conn.close()

        elapsed = time.time() - t0
        total_methods = sum(len(r[3]) for r in results)
        total_calls = sum(len(r[4]) for r in results)
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

        if not to_remove and not to_add:
            conn.close()
            return {"added": 0, "changed": 0, "removed": 0}

        # Process new/changed files
        results: list[tuple[BslFileInfo, float, int, list[dict], list[tuple[int, str, int]]]] = []
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

        # Apply changes in a single transaction
        with conn:
            # Delete old data for removed + changed
            if to_remove:
                for rel in to_remove:
                    mod_info = db_modules.get(rel)
                    if mod_info is None:
                        continue
                    mod_id = mod_info["id"]
                    # Get method IDs for cascade delete of calls
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
                    conn.execute("DELETE FROM methods WHERE module_id = ?", (mod_id,))
                    conn.execute("DELETE FROM modules WHERE id = ?", (mod_id,))

            # Insert new data
            self._bulk_insert(conn, results, build_calls)

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
            )
            md_tables = _collect_metadata_tables(base_path)
            _insert_metadata_tables(conn, md_tables)

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
        results: list[tuple[BslFileInfo, float, int, list[dict], list[tuple[int, str, int]]]],
        build_calls: bool,
    ) -> None:
        """Insert modules, methods, and calls in batch."""
        module_rows: list[tuple] = []
        for info, mtime, size, _methods, _calls in results:
            module_rows.append((
                info.relative_path,
                info.category,
                info.object_name,
                info.module_type,
                info.form_name,
                1 if info.is_form_module else 0,
                mtime,
                size,
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

        for info, _mtime, _size, methods, raw_calls in results:
            mod_id = path_to_id.get(info.relative_path)
            if mod_id is None:
                continue
            for method in methods:
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
                for method_idx, callee_name, call_line in raw_calls:
                    call_pending.append((info.relative_path, method_idx, callee_name, call_line))

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
            for info, _mtime, _size, methods, _raw_calls in results:
                mod_id = path_to_id.get(info.relative_path)
                if mod_id is None:
                    continue
                method_ids = methods_by_module.get(mod_id, [])

                for method_idx, callee_name, call_line in _raw_calls:
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
        ]
        # Level-1: Configuration metadata
        if config_meta:
            for key, value in config_meta.items():
                meta_entries.append((key, value))

        conn.executemany(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
            meta_entries,
        )
        conn.commit()


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
            count_query = f"SELECT COUNT(*) AS cnt FROM ({query})"
            count_row = self._conn.execute(count_query, params_list).fetchone()
            total_callers = count_row["cnt"] if count_row else 0

            # Fetch page
            query += " ORDER BY mod.rel_path, call_line LIMIT ? OFFSET ?"
            params_list.extend([limit, offset])

            rows = self._conn.execute(query, params_list).fetchall()

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
                        "config_vendor", "source_format", "config_role", "has_metadata"):
                meta_row = self._conn.execute(
                    "SELECT value FROM index_meta WHERE key = ?", (key,)
                ).fetchone()
                stats[key] = meta_row["value"] if meta_row else None

            # Level-2 metadata counts
            for table in ("event_subscriptions", "scheduled_jobs", "functional_options"):
                try:
                    row = self._conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()  # noqa: S608
                    stats[table] = row["cnt"] if row else 0
                except sqlite3.Error:
                    stats[table] = 0

            return stats

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
