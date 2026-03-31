from __future__ import annotations
import concurrent.futures
import json
import logging
import os
import re
import threading
import time as _time_mod
from pathlib import Path
from rlm_tools_bsl.format_detector import parse_bsl_path, BslFileInfo, FormatInfo
from rlm_tools_bsl.bsl_knowledge import BSL_PATTERNS
from rlm_tools_bsl.cache import load_index, save_index

logger = logging.getLogger(__name__)
from rlm_tools_bsl.bsl_xml_parsers import (
    _normalize_category,
    parse_metadata_xml,
    parse_event_subscription_xml,
    parse_scheduled_job_xml,
    parse_enum_xml,
    parse_functional_option_xml,
    parse_rights_xml,
)


class LazyList:
    """Thread-safe lazy-init list with double-check locking."""

    __slots__ = ("data", "_built", "_lock")

    def __init__(self):
        self.data: list = []
        self._built = False
        self._lock = threading.Lock()

    def ensure(self, builder):
        if self._built:
            return self.data
        with self._lock:
            if not self._built:
                self.data.extend(builder())
                self._built = True
        return self.data


class LazyDict:
    """Thread-safe per-key lazy cache with double-check locking."""

    __slots__ = ("data", "_lock")

    def __init__(self):
        self.data: dict = {}
        self._lock = threading.Lock()

    def get_or_set(self, key, builder):
        if key in self.data:
            return self.data[key]
        with self._lock:
            if key not in self.data:
                self.data[key] = builder()
        return self.data[key]


def make_bsl_helpers(
    base_path: str,
    resolve_safe,  # callable: str -> pathlib.Path
    read_file_fn,  # callable: str -> str
    grep_fn,  # callable: (pattern, path) -> list[dict]
    glob_files_fn,  # callable: (pattern) -> list[str]
    format_info: FormatInfo | None = None,
    idx_reader=None,  # optional IndexReader for SQLite index acceleration
    idx_zero_callers_authoritative: bool = False,
) -> dict:
    """Creates BSL helper functions for sandbox namespace.
    Internal _bsl_index is built lazily on first find_module() call.
    If idx_reader is provided, helpers use it as a fast path with fallback."""

    # Mutable closure state for lazy index
    _index_state: list = []  # list of tuples (relative_path, BslFileInfo)
    _index_built: list[bool] = [False]
    _index_lock = threading.Lock()

    def _ensure_index() -> None:
        if _index_built[0]:
            return
        with _index_lock:
            if _index_built[0]:
                return

            # Fast path: load from SQLite index (instant, <1s)
            if idx_reader is not None:
                try:
                    rows = idx_reader.get_all_modules()
                    for r in rows:
                        info = BslFileInfo(
                            relative_path=r["rel_path"],
                            category=r["category"],
                            object_name=r["object_name"],
                            module_type=r["module_type"],
                            form_name=r["form_name"],
                            command_name=None,
                            is_form_module=bool(r["form_name"]),
                        )
                        _index_state.append((r["rel_path"], info))
                    _index_built[0] = True
                    return
                except Exception:
                    pass  # fallback to glob

            # Fallback: glob + disk cache
            all_bsl = glob_files_fn("**/*.bsl")
            bsl_count = len(all_bsl)

            cached = load_index(base_path, bsl_count, bsl_paths=all_bsl)
            if cached is not None:
                _index_state.extend(cached)
            else:
                for file_path in all_bsl:
                    info = parse_bsl_path(file_path, base_path)
                    _index_state.append((info.relative_path, info))
                save_index(base_path, bsl_count, _index_state)

            _index_built[0] = True

    # --- Auto-detect custom prefixes from object names ---
    _detected_prefixes: list[str] = []
    _prefixes_built: list[bool] = [False]
    _prefixes_lock = threading.Lock()

    def _ensure_prefixes() -> list[str]:
        if _prefixes_built[0]:
            return _detected_prefixes
        with _prefixes_lock:
            if _prefixes_built[0]:
                return _detected_prefixes
            _ensure_index()

            # Collect unique object names from index
            object_names: set[str] = set()
            for _, info in _index_state:
                if info.object_name:
                    object_names.add(info.object_name)

            # Custom objects start with a lowercase letter in 1C conventions.
            # Extract prefix: sequence of lowercase letters (+ optional _) before
            # the first uppercase letter.
            prefix_re = re.compile(r"^([a-zа-яё]+_?)")
            prefix_counts: dict[str, int] = {}
            for name in object_names:
                if not name or not name[0].islower():
                    continue
                m = prefix_re.match(name)
                if m:
                    prefix = m.group(1)
                    # Normalize: strip trailing _ for counting, keep in result
                    key = prefix.rstrip("_").lower()
                    if len(key) >= 2:
                        prefix_counts[key] = prefix_counts.get(key, 0) + 1

            # For extensions, lower threshold to 1 (fewer custom objects expected)
            config_role = None
            if idx_reader is not None:
                try:
                    config_role = idx_reader.get_statistics().get("config_role")
                except Exception:
                    pass
            min_count = 1 if config_role == "extension" else 3

            frequent = sorted(
                ((k, v) for k, v in prefix_counts.items() if v >= min_count),
                key=lambda x: -x[1],
            )
            _detected_prefixes.clear()
            _detected_prefixes.extend(k for k, _ in frequent)

            _prefixes_built[0] = True
            return _detected_prefixes

    # --- Strip 1C metadata type prefixes from object names ---
    # Models often pass "Документ.РеализацияТоваровУслуг" instead of "РеализацияТоваровУслуг"
    _META_TYPE_PREFIXES = (
        "Документ.",
        "Справочник.",
        "Перечисление.",
        "РегистрСведений.",
        "РегистрНакопления.",
        "РегистрБухгалтерии.",
        "РегистрРасчета.",
        "Отчет.",
        "Обработка.",
        "ПланОбмена.",
        "ПланСчетов.",
        "ПланВидовХарактеристик.",
        "ПланВидовРасчета.",
        "БизнесПроцесс.",
        "Задача.",
        "Константа.",
        "ПодпискаНаСобытие.",
        "РегламентноеЗадание.",
        "Document.",
        "Catalog.",
        "Enum.",
        "InformationRegister.",
        "AccumulationRegister.",
        "AccountingRegister.",
        "CalculationRegister.",
        "Report.",
        "DataProcessor.",
        "ExchangePlan.",
        "ChartOfAccounts.",
        "ChartOfCharacteristicTypes.",
        "ChartOfCalculationTypes.",
        "BusinessProcess.",
        "Task.",
        "Constant.",
        "DocumentObject.",
        "CatalogObject.",
        "DocumentRef.",
        "CatalogRef.",
        "ДокументОбъект.",
        "СправочникОбъект.",
        "ДокументСсылка.",
        "СправочникСсылка.",
        "ОбщаяФорма.",
        "CommonForm.",
    )

    def _strip_meta_prefix(name: str) -> str:
        """Strip 1C metadata type prefix if present: 'Документ.X' -> 'X'."""
        for prefix in _META_TYPE_PREFIXES:
            if name.startswith(prefix):
                return name[len(prefix) :]
        return name

    def _info_to_dict(relative_path: str, info: BslFileInfo) -> dict:
        return {
            "path": relative_path,
            "category": info.category,
            "object_name": info.object_name,
            "module_type": info.module_type,
            "form_name": info.form_name,
        }

    # ── Helper registry ──────────────────────────────────────────
    _registry: dict[str, dict] = {}

    def _reg(name: str, fn, sig: str, cat: str, kw: list[str] | None = None, recipe: str = ""):
        """Register a helper: sig for strategy table, kw+recipe for help()."""
        _registry[name] = {
            "fn": fn,
            "sig": sig,
            "cat": cat,
            "kw": kw or [],
            "recipe": recipe,
        }

    def find_module(name: str) -> list[dict]:
        """Find BSL modules by name fragment (case-insensitive).

        Returns: list of dicts {path, category, object_name, module_type, form_name}."""
        name = _strip_meta_prefix(name)
        _ensure_index()
        name_lower = name.lower()
        results = []
        for relative_path, info in _index_state:
            matched = False
            if info.object_name and name_lower in info.object_name.lower():
                matched = True
            if not matched and name_lower in relative_path.lower():
                matched = True
            if matched:
                results.append(_info_to_dict(relative_path, info))
            if len(results) >= 50:
                break
        return results

    def find_by_type(meta_type: str, name: str = "") -> list[dict]:
        """Find BSL modules by metadata category, optionally filtered by object name.

        Accepts plural folder names (InformationRegisters), singular (InformationRegister),
        and Russian names (РегистрСведений).
        Categories: CommonModules, Documents, Catalogs, InformationRegisters,
        AccumulationRegisters, AccountingRegisters, CalculationRegisters,
        Reports, DataProcessors, Constants.

        Returns: list of dicts {path, category, object_name, module_type, form_name}."""
        name = _strip_meta_prefix(name)
        _ensure_index()
        meta_type_lower = _normalize_category(meta_type)
        name_lower = name.lower()
        results = []
        for relative_path, info in _index_state:
            if not info.category or info.category.lower() != meta_type_lower:
                continue
            if name_lower and (not info.object_name or name_lower not in info.object_name.lower()):
                continue
            results.append(_info_to_dict(relative_path, info))
            if len(results) >= 50:
                break
        return results

    _proc_lazy = LazyDict()
    _prefilter_lazy = LazyDict()

    def _parse_procedures(path: str) -> list[dict]:
        """Parse BSL file — internal, result gets cached by LazyDict."""
        content = read_file_fn(path)
        lines = content.splitlines()

        proc_def_re = re.compile(BSL_PATTERNS["procedure_def"], re.IGNORECASE)
        proc_end_re = re.compile(BSL_PATTERNS["procedure_end"], re.IGNORECASE)

        procedures = []
        current: dict | None = None

        for line_idx, line in enumerate(lines):
            line_number = line_idx + 1  # 1-based

            if current is None:
                m = proc_def_re.search(line)
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
                m_end = proc_end_re.search(line)
                if m_end:
                    current["end_line"] = line_number
                    procedures.append(current)
                    current = None

        # Handle unclosed procedure at EOF
        if current is not None:
            current["end_line"] = len(lines)
            procedures.append(current)

        return procedures

    def extract_procedures(path: str) -> list[dict]:
        """Parse BSL file and return list of procedures/functions with metadata.
        Results are memoized per file path within the session.
        Uses SQLite index when available (instant), falls back to regex parsing.

        Returns: list of dicts {name, type, line, end_line, is_export, params, overridden_by?}."""

        def _extract_with_index():
            if idx_reader is not None:
                result = idx_reader.get_methods_by_path(path)
                if result is not None:
                    # Enrich with override data from index
                    try:
                        overrides_map = idx_reader.get_overrides_for_path(path)
                        if overrides_map:
                            # Build case-insensitive lookup (Cyrillic)
                            ov_lower = {k.lower(): v for k, v in overrides_map.items()}
                            for proc in result:
                                method_overrides = ov_lower.get(proc["name"].lower())
                                if method_overrides:
                                    proc["overridden_by"] = [
                                        {
                                            "annotation": ov.get("annotation", ""),
                                            "extension_name": ov.get("extension_name", ""),
                                            "extension_method": ov.get("extension_method", ""),
                                            "extension_root": ov.get("extension_root", ""),
                                            "ext_module_path": ov.get("ext_module_path", ""),
                                            "ext_line": ov.get("ext_line"),
                                        }
                                        for ov in method_overrides
                                    ]
                    except Exception:
                        pass  # opportunistic enrichment
                    return result
            return _parse_procedures(path)

        return _proc_lazy.get_or_set(path, _extract_with_index)

    def find_exports(path: str) -> list[dict]:
        """Return only exported procedures/functions from a BSL file.

        Returns: list of dicts {name, type, line, end_line, is_export, params}."""
        return [p for p in extract_procedures(path) if p["is_export"]]

    def safe_grep(pattern: str, name_hint: str = "", max_files: int = 20) -> list[dict]:
        """Timeout-safe grep across BSL files, optionally scoped by module name hint."""
        _ensure_index()

        if name_hint:
            candidates = find_module(name_hint)
            paths = [c["path"] for c in candidates[:max_files]]
        else:
            paths = [relative_path for relative_path, _ in _index_state[:max_files]]

        def _grep_one(path: str) -> list[dict]:
            try:
                return grep_fn(pattern, path) or []
            except Exception:
                return []

        if len(paths) > 1:
            from concurrent.futures import ThreadPoolExecutor as _TP

            with _TP(max_workers=min(8, len(paths))) as pool:
                all_results = list(pool.map(_grep_one, paths))
            results = [m for batch in all_results for m in batch]
        elif paths:
            results = _grep_one(paths[0])
        else:
            results = []

        # Deterministic order: sort by (file, line)
        results.sort(key=lambda m: (m.get("file", ""), m.get("line", 0)))
        return results

    def read_procedure(path: str, proc_name: str, include_overrides: bool = False) -> str | None:
        """Extract a single procedure body from a BSL file by name.
        With include_overrides=True, appends extension override bodies if available."""
        procedures = extract_procedures(path)
        target = None
        for p in procedures:
            if p["name"].lower() == proc_name.lower():
                target = p
                break
        if target is None:
            return None

        content = read_file_fn(path)
        lines = content.splitlines()

        start = target["line"] - 1  # convert to 0-based
        end = target["end_line"] if target["end_line"] is not None else len(lines)
        # end_line is 1-based and inclusive
        extracted = lines[start:end]
        body = "\n".join(extracted)

        if not include_overrides:
            return body

        # Enrich with extension override bodies
        override_list = target.get("overridden_by")
        if not override_list and idx_reader is not None:
            try:
                overrides_map = idx_reader.get_overrides_for_path(path)
                # Case-insensitive lookup (Cyrillic)
                ov_lower = {k.lower(): v for k, v in overrides_map.items()}
                override_list = ov_lower.get(target["name"].lower())
            except Exception:
                override_list = None

        if not override_list:
            return body

        from rlm_tools_bsl.extension_detector import detect_extension_context as _det_ctx

        try:
            ext_context = _det_ctx(base_path)
        except Exception:
            return body

        trusted_roots: set[Path] = set()
        for e in ext_context.nearby_extensions:
            trusted_roots.add(Path(e.path).resolve())
        trusted_roots.add(Path(ext_context.current.path).resolve())

        parts = [body]
        for ov in override_list:
            ext_root = ov.get("extension_root", "")
            ext_mod = ov.get("ext_module_path", "")
            annotation = ov.get("annotation", "")
            ext_name = ov.get("extension_name", "")
            ext_method = ov.get("extension_method", "")
            ext_line = ov.get("ext_line")

            header = f'\n// === Перехвачен &{annotation} в расширении "{ext_name}" ==='
            file_ref = f"// Файл: {ext_name}/{ext_mod}"
            if ext_line:
                file_ref += f":{ext_line}"

            # Try to read extension method body
            ext_body = None
            if ext_root and ext_mod:
                candidate = Path(ext_root, ext_mod).resolve()
                if any(candidate.is_relative_to(root) for root in trusted_roots):
                    try:
                        ext_content = candidate.read_text(encoding="utf-8-sig", errors="replace")
                        ext_lines = ext_content.splitlines()
                        # Find method by name in extension file
                        proc_def_re = re.compile(BSL_PATTERNS["procedure_def"], re.IGNORECASE)
                        proc_end_re = re.compile(BSL_PATTERNS["procedure_end"], re.IGNORECASE)
                        search_name = (ext_method or "").lower()
                        in_target = False
                        start_idx = None
                        for i, ln in enumerate(ext_lines):
                            if not in_target:
                                m = proc_def_re.search(ln)
                                if m and m.group(2).lower() == search_name:
                                    in_target = True
                                    start_idx = i
                            else:
                                if proc_end_re.search(ln):
                                    ext_body = "\n".join(ext_lines[start_idx : i + 1])
                                    break
                        if in_target and ext_body is None and start_idx is not None:
                            ext_body = "\n".join(ext_lines[start_idx:])
                    except OSError:
                        pass

            parts.append(header)
            parts.append(file_ref)
            if ext_body:
                parts.append(ext_body)

        return "\n".join(parts)

    def find_callers(proc_name: str, module_hint: str = "", max_files: int = 20) -> list[dict]:
        """Find all callers of a procedure by name across BSL files.
        Delegates to find_callers_context for thorough cross-module search.

        Returns: list of dicts {file, line, text}."""
        result = find_callers_context(proc_name, module_hint, 0, max_files)
        return [{"file": c["file"], "line": c["line"], "text": c.get("context", "")} for c in result["callers"]]

    # --- Parallel prefilter for find_callers_context ---
    _base = Path(base_path)

    def _parallel_prefilter(
        files: list[tuple[str, BslFileInfo]],
        needle: str,
        base: str,
        max_workers: int = 12,
    ) -> list[tuple[str, BslFileInfo]]:
        """Scan all BSL files for substring in parallel using ThreadPoolExecutor.
        Bypasses sandbox read_file to avoid cache contention between threads.
        All paths come from the trusted index (built from glob inside base_path)."""
        base_p = Path(base)

        def _check(item: tuple[str, BslFileInfo]) -> tuple[str, BslFileInfo] | None:
            rel, info = item
            try:
                full = base_p / rel
                with open(full, "r", encoding="utf-8-sig", errors="replace") as f:
                    content = f.read()
                if needle in content.lower():
                    return (rel, info)
            except Exception:
                pass
            return None

        matched: list[tuple[str, BslFileInfo]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            for result in pool.map(_check, files):
                if result is not None:
                    matched.append(result)
        return matched

    # --- Regex for stripping comments and string literals ---
    _re_string_literal = re.compile(r'"[^"\r\n]*"')

    def _strip_code_line(line: str) -> str:
        """Remove comments and string literals from a BSL code line."""
        # Strip comment (// with or without space)
        ci = line.find("//")
        if ci >= 0:
            line = line[:ci]
        # Strip string literals
        line = _re_string_literal.sub("", line)
        return line

    def find_callers_context(
        proc_name: str,
        module_hint: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Find callers of a procedure with full context: which procedure
        in which module calls the target. Returns structured result with
        caller_name, caller_is_export, file metadata, and pagination info.

        Unlike find_callers() which is a flat grep, this helper identifies
        the exact calling procedure and filters out comments/strings.
        Uses SQLite call graph index when available (instant).

        Args:
            proc_name: Name of the target procedure/function.
            module_hint: Optional module name to determine export scope.
            offset: File offset for pagination (0-based).
            limit: Max files to scan per call (default 50).

        Returns:
            dict with "callers" list and "_meta" pagination info.
        """
        # --- Fast path: SQLite call graph ---
        if idx_reader is not None and idx_reader.has_calls:
            _t0 = _time_mod.monotonic()
            result = idx_reader.get_callers(proc_name, module_hint, offset, limit)
            _elapsed = _time_mod.monotonic() - _t0
            if result is not None:
                _n = len(result.get("callers", []))
                logger.debug(
                    "find_callers_context: proc=%s source=index rows=%d time=%.2fs",
                    proc_name,
                    _n,
                    _elapsed,
                )
                if _n > 0:
                    return result
                if idx_zero_callers_authoritative:
                    logger.debug(
                        "find_callers_context: proc=%s index=0, authoritative=True, skip FS fallback",
                        proc_name,
                    )
                    result["_meta"]["fallback_skipped"] = True
                    result["_meta"]["hint"] = (
                        "No callers found in call index. Use safe_grep(proc_name) to search for text mentions."
                    )
                    return result
                # Untrusted/stale index — fall back to FS scan
                logger.debug(
                    "find_callers_context: proc=%s index returned 0, falling back to scan",
                    proc_name,
                )
            else:
                logger.debug(
                    "find_callers_context: proc=%s source=index returned_none time=%.2fs, falling back to scan",
                    proc_name,
                    _elapsed,
                )

        _ensure_index()

        name_esc = re.escape(proc_name)
        # Patterns: direct call, qualified call (Module.Proc)
        call_patterns = [
            re.compile(r"(?<!\w)" + name_esc + r"\s*\(", re.IGNORECASE),
            re.compile(r"\." + name_esc + r"\s*\(", re.IGNORECASE),
            re.compile(r"(?<!\w)" + name_esc + r"(?!\w)", re.IGNORECASE),
        ]

        # --- Step 1: Determine scope based on export status ---
        target_files: list[str] | None = None  # None = search all

        if module_hint:
            hint_modules = find_module(module_hint)
            if hint_modules:
                # Find the target procedure in hint modules
                for hm in hint_modules:
                    try:
                        procs = extract_procedures(hm["path"])
                        for p in procs:
                            if p["name"].lower() == proc_name.lower():
                                if not p["is_export"] or "Form" in (hm.get("module_type") or ""):
                                    # Not exported or form module -> only search same file
                                    target_files = [hm["path"]]
                                break
                    except Exception:
                        pass
                    if target_files is not None:
                        break

        # --- Step 2: Build candidate file list ---
        if target_files is not None:
            # Scoped to specific files (non-export or form)
            candidate_files = [(rel, info) for rel, info in _index_state if rel in target_files]
        else:
            candidate_files = list(_index_state)

        # --- Step 3: Prefilter by substring (parallel scan, cached) ---
        proc_lower = proc_name.lower()

        if target_files is not None:
            # Scoped search — don't use global prefilter cache
            filtered_files: list[tuple[str, BslFileInfo]] = []
            for rel, info in candidate_files:
                try:
                    content = read_file_fn(rel)
                    if proc_lower in content.lower():
                        filtered_files.append((rel, info))
                except Exception:
                    pass
        else:
            filtered_files = _prefilter_lazy.get_or_set(
                proc_lower,
                lambda: _parallel_prefilter(candidate_files, proc_lower, base_path),
            )

        total_files = len(filtered_files)

        # --- Step 4: Apply pagination ---
        page_files = filtered_files[offset : offset + limit]
        scanned_files = len(page_files)

        # --- Step 5: Scan each file for callers ---
        callers: list[dict] = []

        for rel, info in page_files:
            try:
                content = read_file_fn(rel)
                lines = content.splitlines()
                procs = extract_procedures(rel)

                for proc in procs:
                    # Skip the definition line itself
                    body_start = proc["line"]  # 1-based, this is the def line
                    body_end = proc["end_line"] if proc["end_line"] else len(lines)

                    for line_idx in range(body_start, body_end):  # body_start is def line (skip it)
                        if line_idx >= len(lines):
                            break
                        raw_line = lines[line_idx]
                        cleaned = _strip_code_line(raw_line)
                        if not cleaned.strip():
                            continue

                        for pattern in call_patterns:
                            if pattern.search(cleaned):
                                callers.append(
                                    {
                                        "file": rel,
                                        "caller_name": proc["name"],
                                        "caller_is_export": proc["is_export"],
                                        "line": line_idx + 1,  # 1-based
                                        "context": raw_line.rstrip(),
                                        "object_name": info.object_name,
                                        "category": info.category,
                                        "module_type": info.module_type,
                                    }
                                )
                                break  # one match per line is enough
            except Exception:
                pass

        logger.debug(
            "find_callers_context: proc=%s source=fallback callers=%d files_scanned=%d files_total=%d",
            proc_name,
            len(callers),
            scanned_files,
            total_files,
        )
        return {
            "callers": callers,
            "_meta": {
                "total_callers": len(callers),
                "returned": len(callers),
                "offset": offset,
                "has_more": (offset + limit) < total_files,
            },
        }

    # XML file names by metadata category (CF format: Ext/<name>.xml)
    _CATEGORY_XML_NAMES = {
        "documents": "Document",
        "catalogs": "Catalog",
        "informationregisters": "RecordSet",
        "accumulationregisters": "RecordSet",
        "accountingregisters": "RecordSet",
        "calculationregisters": "RecordSet",
        "reports": "Report",
        "dataprocessors": "DataProcessor",
        "exchangeplans": "ExchangePlan",
        "chartsofaccounts": "ChartOfAccounts",
        "chartsofcharacteristictypes": "ChartOfCharacteristicTypes",
        "chartsofcalculationtypes": "ChartOfCalculationTypes",
        "businessprocesses": "BusinessProcess",
        "tasks": "Task",
        "constants": "Constant",
    }

    def _resolve_object_xml(path: str) -> str:
        """Resolve path to the actual XML file.

        Accepts:
          - Direct path: 'Documents/Name/Ext/Document.xml' → as-is
          - Directory path: 'Documents/Name' → tries Ext/<Type>.xml, then .xml, then .mdo
          - Object path without Ext: 'Documents/Name.xml' → as-is
        """
        path_lower = path.lower().replace("\\", "/")
        if path_lower.endswith(".xml") or path_lower.endswith(".mdo"):
            return path

        # Try CF format: <path>/Ext/<Type>.xml
        parts = path.replace("\\", "/").split("/")
        category = parts[0].lower() if parts else ""
        xml_name = _CATEGORY_XML_NAMES.get(category)

        last_segment = parts[-1] if parts else ""

        candidates = []
        if xml_name:
            candidates.append(f"{path}/Ext/{xml_name}.xml")
        # EDT format: Documents/Name/Name.mdo
        if last_segment:
            candidates.append(f"{path}/{last_segment}.mdo")
        # Generic fallbacks
        candidates.append(f"{path}.xml")
        candidates.append(f"{path}.mdo")
        # Try glob for any XML in Ext/ (CF) or any .mdo (EDT)
        candidates_glob = glob_files_fn(f"{path}/Ext/*.xml")
        if candidates_glob:
            candidates.extend(candidates_glob[:1])
        candidates_mdo = glob_files_fn(f"{path}/*.mdo")
        if candidates_mdo:
            candidates.extend(candidates_mdo[:1])

        for candidate in candidates:
            try:
                if resolve_safe(candidate).exists():
                    return candidate
            except Exception:
                continue

        return path  # return original, let read_file_fn produce the error

    def parse_object_xml(path: str) -> dict:
        """Read a 1C metadata XML file and extract its structure:
        name, synonym, attributes, tabular sections, dimensions, resources,
        subsystem content. Works with any metadata XML (catalogs, documents,
        registers, subsystems, etc.).

        Accepts both direct XML paths and directory paths:
          parse_object_xml('Documents/Name/Ext/Document.xml')  — direct
          parse_object_xml('Documents/Name')                    — auto-resolves

        Returns: dict with keys like name, synonym, attributes, tabular_sections,
        dimensions, resources (depends on metadata type)."""
        resolved = _resolve_object_xml(path)
        content = read_file_fn(resolved)
        return parse_metadata_xml(content)

    # ── Composite helpers (wrappers over existing functions) ────────

    def analyze_subsystem(name: str) -> dict:
        """Find a subsystem by name, parse its XML composition,
        classify objects as custom (non-standard prefix) or standard.

        Returns: dict with subsystems_found, subsystems list."""
        name = _strip_meta_prefix(name)

        # --- Fast path: SQLite index ---
        if idx_reader is not None:
            matches = idx_reader.get_subsystems_for_object(name)
            if matches is not None:
                # matches is [] or list of dicts
                results = []
                for m in matches:
                    results.append(
                        {
                            "file": m["file"],
                            "name": m["name"],
                            "synonym": m["synonym"],
                            "total_objects": len(m["matched_refs"]),
                            "matched_refs": m["matched_refs"],
                        }
                    )
                if not results:
                    return {
                        "error": f"Подсистема с '{name}' не найдена",
                        "hint": "Объект не входит ни в одну подсистему",
                    }
                return {"subsystems_found": len(results), "subsystems": results}

        # --- Fallback: glob + XML parse ---
        patterns = [
            f"**/Subsystems/**/*{name}*",
            f"**/Subsystems/*{name}*",
            # REMOVED: f"**/*{name}*.mdo" — scans entire tree, useless for subsystems
        ]
        found_files: list[str] = []
        for p in patterns:
            found_files.extend(glob_files_fn(p))

        subsystem_files = list(
            dict.fromkeys(f for f in found_files if "Subsystem" in f and (f.endswith(".xml") or f.endswith(".mdo")))
        )

        if not subsystem_files:
            return {
                "error": f"Подсистема '{name}' не найдена",
                "hint": "Попробуйте glob_files('**/Subsystems/**') для просмотра всех подсистем",
            }

        results = []
        for sf in subsystem_files:
            try:
                meta = parse_object_xml(sf)
            except Exception:
                continue
            if not meta or meta.get("object_type") != "Subsystem":
                continue

            content = meta.get("content", [])
            custom_objects = []
            standard_objects = []
            for item in content:
                parts = item.split(".", 1)
                obj_type = parts[0] if parts else ""
                obj_name = parts[1] if len(parts) > 1 else item
                is_custom = bool(obj_name) and obj_name[0].islower()
                entry = {"type": obj_type, "name": obj_name, "is_custom": is_custom}
                if is_custom:
                    custom_objects.append(entry)
                else:
                    standard_objects.append(entry)

            results.append(
                {
                    "file": sf,
                    "name": meta.get("name", ""),
                    "synonym": meta.get("synonym", ""),
                    "total_objects": len(content),
                    "custom_objects": custom_objects,
                    "standard_objects": standard_objects,
                    "raw_content": content,
                }
            )

        return {"subsystems_found": len(results), "subsystems": results}

    def find_custom_modifications(
        object_name: str,
        custom_prefixes: list[str] | None = None,
    ) -> dict:
        """Find all non-standard (custom) modifications in an object's modules:
        procedures with custom prefix, custom #Область regions, custom XML attributes.
        If custom_prefixes is not provided, uses auto-detected prefixes from the codebase.

        Returns: dict with modifications list and custom_attributes."""
        object_name = _strip_meta_prefix(object_name)
        prefix_source = "user" if custom_prefixes else "auto"
        prefixes = custom_prefixes or _ensure_prefixes()
        if not prefixes:
            return {"error": "Нетиповые префиксы не обнаружены. Укажите custom_prefixes вручную."}

        modules = find_module(object_name)
        exact = [m for m in modules if (m.get("object_name") or "").lower() == object_name.lower()]
        if not exact:
            exact = modules
        if not exact:
            return {"error": f"Объект '{object_name}' не найден"}

        def _match_prefix(s: str) -> bool:
            sl = s.lower()
            return any(sl.startswith(p.lower()) for p in prefixes)

        modifications = []
        for mod in exact:
            path = mod["path"]
            try:
                procs = extract_procedures(path)
            except Exception:
                continue

            custom_procs = [p for p in procs if _match_prefix(p["name"])]

            custom_regions: list[dict] = []
            try:
                content = read_file_fn(path)
                for i, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#") and "Область" in stripped:
                        region_name = stripped.split("Область", 1)[1].strip()
                        if _match_prefix(region_name):
                            custom_regions.append({"name": region_name, "line": i})
            except Exception:
                pass

            if custom_procs or custom_regions:
                modifications.append(
                    {
                        "path": path,
                        "module_type": mod.get("module_type", ""),
                        "form_name": mod.get("form_name"),
                        "total_procedures": len(procs),
                        "custom_procedures": custom_procs,
                        "custom_regions": custom_regions,
                    }
                )

        custom_attributes: list[dict] = []
        parse_error: str | None = None
        category = exact[0].get("category", "")
        obj_name = exact[0].get("object_name", "")
        if category and obj_name:
            try:
                meta = parse_object_xml(f"{category}/{obj_name}")
                for attr in meta.get("attributes", []):
                    if _match_prefix(attr["name"]):
                        custom_attributes.append(attr)
                for ts in meta.get("tabular_sections", []):
                    if _match_prefix(ts["name"]):
                        custom_attributes.append(
                            {
                                "name": ts["name"],
                                "type": "TabularSection",
                                "synonym": ts.get("synonym", ""),
                            }
                        )
            except Exception as exc:
                parse_error = f"{type(exc).__name__}: {exc}"

        result = {
            "object_name": object_name,
            "prefixes_used": prefixes,
            "prefix_source": prefix_source,
            "modules_analyzed": len(exact),
            "modifications": modifications,
            "custom_attributes": custom_attributes,
        }
        if parse_error:
            result["parse_error"] = parse_error
        return result

    def analyze_object(name: str) -> dict:
        """Full object profile in one call: XML metadata + all modules + procedures + exports.

        Returns: dict with name, category, metadata, modules."""
        name = _strip_meta_prefix(name)
        modules = find_module(name)
        exact = [m for m in modules if (m.get("object_name") or "").lower() == name.lower()]
        if not exact:
            exact = modules[:20]
        if not exact:
            return {"error": f"Объект '{name}' не найден"}

        category = exact[0].get("category", "")
        obj_name = exact[0].get("object_name", "")

        metadata: dict = {}
        if category and obj_name:
            try:
                metadata = parse_object_xml(f"{category}/{obj_name}")
            except Exception:
                pass

        module_details = []
        for mod in exact:
            path = mod["path"]
            try:
                procs = extract_procedures(path)
                exports = [p for p in procs if p.get("is_export")]
            except Exception:
                procs, exports = [], []

            module_details.append(
                {
                    "path": path,
                    "module_type": mod.get("module_type", ""),
                    "form_name": mod.get("form_name"),
                    "procedures_count": len(procs),
                    "exports_count": len(exports),
                    "procedures": procs,
                    "exports": exports,
                }
            )

        return {
            "name": obj_name,
            "category": category,
            "metadata": metadata,
            "modules": module_details,
        }

    # ── Business-process helpers ─────────────────────────────────

    _event_sub_lazy = LazyList()

    def _build_event_subscriptions() -> list[dict]:
        files = glob_files_fn("**/EventSubscriptions/**/*.xml")
        files.extend(glob_files_fn("**/EventSubscriptions/**/*.mdo"))
        files = list(dict.fromkeys(files))
        result: list[dict] = []
        for f in files:
            try:
                content = read_file_fn(f)
            except Exception:
                continue
            parsed = parse_event_subscription_xml(content)
            if parsed is None:
                continue
            handler = parsed["handler"]
            parts = handler.rsplit(".", 1)
            handler_procedure = parts[-1] if parts else handler
            handler_module = ""
            if len(parts) > 1:
                module_part = parts[0]
                if module_part.startswith("CommonModule."):
                    module_part = module_part[len("CommonModule.") :]
                handler_module = module_part
            result.append(
                {
                    "name": parsed["name"],
                    "synonym": parsed["synonym"],
                    "source_types": parsed["source_types"],
                    "source_count": len(parsed["source_types"]),
                    "event": parsed["event"],
                    "handler": handler,
                    "handler_module": handler_module,
                    "handler_procedure": handler_procedure,
                    "file": f,
                }
            )
        return result

    def _ensure_event_subscriptions() -> list[dict]:
        return _event_sub_lazy.ensure(_build_event_subscriptions)

    def find_event_subscriptions(
        object_name: str = "",
        custom_only: bool = False,
    ) -> list[dict]:
        """Find event subscriptions, optionally filtered by object name.
        Shows what fires when an object is written/posted/deleted.
        Uses SQLite index when available (instant), falls back to XML parsing.

        Args:
            object_name: Object name to filter by (case-insensitive substring
                         match against source types). Empty = return all.
            custom_only: If True, return only subscriptions whose name starts
                         with a detected custom prefix (auto-detected from codebase).

        Returns: list of dicts with name, synonym, source_count, event,
                 handler, handler_module, handler_procedure, file."""
        if object_name:
            object_name = _strip_meta_prefix(object_name)

        # --- Fast path: SQLite index ---
        if idx_reader is not None:
            idx_result = idx_reader.get_event_subscriptions(object_name)
            if idx_result is not None:
                if custom_only:
                    prefixes = _ensure_prefixes()
                    if prefixes:
                        idx_result = [s for s in idx_result if any(s["name"].lower().startswith(p) for p in prefixes)]
                return idx_result

        all_subs = _ensure_event_subscriptions()

        if not object_name:
            # Return without source_types to keep output compact
            result = [{k: v for k, v in s.items() if k != "source_types"} for s in all_subs]
        else:
            name_lower = object_name.lower()
            result = []
            for s in all_subs:
                # Include subscriptions that explicitly list this object in source_types,
                # OR subscriptions with empty source_types (source_count=0) — these apply
                # to all objects of a given type (catch-all subscriptions).
                if not s["source_types"]:
                    matched = True
                else:
                    matched = any(name_lower in t.lower() for t in s["source_types"])
                if matched:
                    result.append(dict(s))  # include source_types for filtered results

        if custom_only:
            prefixes = _ensure_prefixes()
            if prefixes:
                result = [s for s in result if any(s["name"].lower().startswith(p) for p in prefixes)]

        return result

    _sched_job_lazy = LazyList()

    def _build_scheduled_jobs() -> list[dict]:
        files = glob_files_fn("**/ScheduledJobs/**/*.xml")
        files.extend(glob_files_fn("**/ScheduledJobs/**/*.mdo"))
        files = list(dict.fromkeys(files))
        result: list[dict] = []
        for f in files:
            try:
                content = read_file_fn(f)
            except Exception:
                continue
            parsed = parse_scheduled_job_xml(content)
            if parsed is None:
                continue
            method = parsed["method_name"]
            parts = method.rsplit(".", 1)
            handler_procedure = parts[-1] if parts else method
            handler_module = ""
            if len(parts) > 1:
                module_part = parts[0]
                if module_part.startswith("CommonModule."):
                    module_part = module_part[len("CommonModule.") :]
                handler_module = module_part
            result.append(
                {
                    "name": parsed["name"],
                    "synonym": parsed["synonym"],
                    "method_name": method,
                    "handler_module": handler_module,
                    "handler_procedure": handler_procedure,
                    "use": parsed["use"],
                    "predefined": parsed["predefined"],
                    "restart_on_failure": parsed["restart_on_failure"],
                    "file": f,
                }
            )
        return result

    def _ensure_scheduled_jobs() -> list[dict]:
        return _sched_job_lazy.ensure(_build_scheduled_jobs)

    def find_scheduled_jobs(name: str = "") -> list[dict]:
        """Find scheduled (background) jobs, optionally filtered by name.
        Uses SQLite index when available (instant), falls back to XML parsing.

        Args:
            name: Name substring to filter by (case-insensitive). Empty = all.

        Returns: list of dicts with name, synonym, method_name,
                 handler_module, handler_procedure, use, predefined, file."""
        if name:
            name = _strip_meta_prefix(name)

        # --- Fast path: SQLite index ---
        if idx_reader is not None:
            idx_result = idx_reader.get_scheduled_jobs(name)
            if idx_result is not None:
                return idx_result

        all_jobs = _ensure_scheduled_jobs()
        if not name:
            return all_jobs
        name_lower = name.lower()
        return [j for j in all_jobs if name_lower in j["name"].lower()]

    # ── Integration metadata helpers ─────────────────────────────

    def find_http_services(name: str = "") -> list[dict]:
        """Find HTTP services, optionally filtered by name.
        Uses SQLite index when available, falls back to XML parsing.

        Args:
            name: Name substring to filter by (case-insensitive). Empty = all.

        Returns: list of dicts with name, root_url, templates, file."""
        if name:
            name = _strip_meta_prefix(name)

        # Fast path: SQLite index
        if idx_reader is not None:
            idx_result = idx_reader.get_http_services(name)
            if idx_result is not None:
                return idx_result

        # Fallback: glob + parse
        from rlm_tools_bsl.bsl_xml_parsers import parse_http_service_xml

        files = glob_files_fn("HTTPServices/**/*.xml") + glob_files_fn("HTTPServices/**/*.mdo")
        results: list[dict] = []
        for fp in files:
            content = read_file_fn(fp)
            if not content:
                continue
            parsed = parse_http_service_xml(content)
            if parsed and (not name or name.lower() in parsed["name"].lower()):
                parsed["file"] = fp if not os.path.isabs(fp) else os.path.relpath(fp, base_path).replace("\\", "/")
                results.append(parsed)
        return results

    def find_web_services(name: str = "") -> list[dict]:
        """Find web services (SOAP), optionally filtered by name.
        Uses SQLite index when available, falls back to XML parsing.

        Args:
            name: Name substring to filter by (case-insensitive). Empty = all.

        Returns: list of dicts with name, namespace, operations, file."""
        if name:
            name = _strip_meta_prefix(name)

        # Fast path: SQLite index
        if idx_reader is not None:
            idx_result = idx_reader.get_web_services(name)
            if idx_result is not None:
                return idx_result

        # Fallback: glob + parse
        from rlm_tools_bsl.bsl_xml_parsers import parse_web_service_xml

        files = glob_files_fn("WebServices/**/*.xml") + glob_files_fn("WebServices/**/*.mdo")
        results: list[dict] = []
        for fp in files:
            content = read_file_fn(fp)
            if not content:
                continue
            parsed = parse_web_service_xml(content)
            if parsed and (not name or name.lower() in parsed["name"].lower()):
                parsed["file"] = fp if not os.path.isabs(fp) else os.path.relpath(fp, base_path).replace("\\", "/")
                results.append(parsed)
        return results

    def find_xdto_packages(name: str = "") -> list[dict]:
        """Find XDTO packages, optionally filtered by name.
        Uses SQLite index when available, falls back to XML parsing.

        Args:
            name: Name substring to filter by (case-insensitive). Empty = all.

        Returns: list of dicts with name, namespace, types, file."""
        if name:
            name = _strip_meta_prefix(name)

        # Fast path: SQLite index
        if idx_reader is not None:
            idx_result = idx_reader.get_xdto_packages(name)
            if idx_result is not None:
                return idx_result

        # Fallback: glob + parse
        from rlm_tools_bsl.bsl_xml_parsers import parse_xdto_package_xml, parse_xdto_types

        files = glob_files_fn("XDTOPackages/**/*.xml") + glob_files_fn("XDTOPackages/**/*.mdo")
        results: list[dict] = []
        for fp in files:
            content = read_file_fn(fp)
            if not content:
                continue
            parsed = parse_xdto_package_xml(content)
            if parsed and (not name or name.lower() in parsed["name"].lower()):
                # For EDT: check sibling Package.xdto
                if fp.endswith(".mdo"):
                    xdto_path = os.path.join(os.path.dirname(fp), "Package.xdto")
                    try:
                        xdto_content = read_file_fn(xdto_path)
                    except Exception:
                        xdto_content = None
                    if xdto_content:
                        parsed["types"] = parse_xdto_types(xdto_content)
                parsed["file"] = fp if not os.path.isabs(fp) else os.path.relpath(fp, base_path).replace("\\", "/")
                results.append(parsed)
        return results

    def find_exchange_plan_content(name: str) -> list[dict]:
        """Find exchange plan content (objects registered for exchange).
        Always parses XML at runtime (no index table).

        Args:
            name: Exchange plan name.

        Returns: list of dicts with ref, auto_record."""
        name = _strip_meta_prefix(name)
        from rlm_tools_bsl.bsl_xml_parsers import parse_exchange_plan_content as _parse_ep

        def _valid_files(pattern: str) -> list[str]:
            """Glob and filter out hint strings."""
            return [f for f in glob_files_fn(pattern) if not f.startswith("[")]

        # EDT: .mdo file of the exchange plan itself (content is inline)
        # CF: Ext/Content.xml
        files = (
            _valid_files(f"ExchangePlans/{name}/*.mdo")
            + _valid_files(f"ExchangePlans/{name}/**/*.mdo")
            + _valid_files(f"ExchangePlans/{name}/**/*.xml")
        )
        if not files:
            # Try wildcard search across all exchange plans
            all_files = _valid_files("ExchangePlans/**/*.xml") + _valid_files("ExchangePlans/**/*.mdo")
            name_lower = name.lower()
            files = [f for f in all_files if name_lower in f.lower()]

        results: list[dict] = []
        seen_refs: set[str] = set()
        for fp in files:
            content = read_file_fn(fp)
            if not content:
                continue
            items = _parse_ep(content)
            for item in items:
                if item["ref"] not in seen_refs:
                    results.append(item)
                    seen_refs.add(item["ref"])
        return results

    def find_register_movements(document_name: str) -> dict:
        """Find all registers that a document writes to during posting.
        Searches ObjectModule code for 'Движения.RegisterName' pattern.

        Args:
            document_name: Document name (or fragment).

        Returns: dict with document, code_registers, modules_scanned."""
        document_name = _strip_meta_prefix(document_name)

        # Fast path: SQLite index
        if idx_reader is not None:
            idx_movements = idx_reader.get_register_movements(document_name)
            if idx_movements is not None:
                return {
                    "document": document_name,
                    "code_registers": [
                        {"name": m["register_name"], "source": m["source"], "file": m["file"]}
                        for m in idx_movements
                        if m["source"] == "code"
                    ],
                    "modules_scanned": [],
                    "erp_mechanisms": [m["register_name"] for m in idx_movements if m["source"] == "erp_mechanism"],
                    "manager_tables": [m["register_name"] for m in idx_movements if m["source"] == "manager_table"],
                    "adapted_registers": [m["register_name"] for m in idx_movements if m["source"] == "adapted"],
                }

        modules = find_by_type("Documents", document_name)
        obj_modules = [m for m in modules if m.get("module_type") == "ObjectModule"]

        if not obj_modules:
            return {
                "document": document_name,
                "code_registers": [],
                "modules_scanned": [],
                "error": f"ObjectModule для документа '{document_name}' не найден",
            }

        movement_re = re.compile(r"Движения\.(\w+)", re.IGNORECASE)
        code_registers: dict[str, dict] = {}  # name -> {name, lines, file}
        modules_scanned: list[str] = []

        for mod in obj_modules:
            path = mod["path"]
            modules_scanned.append(path)
            try:
                content = read_file_fn(path)
            except Exception:
                continue
            for i, line in enumerate(content.splitlines(), 1):
                for m in movement_re.finditer(line):
                    reg_name = m.group(1)
                    if reg_name not in code_registers:
                        code_registers[reg_name] = {
                            "name": reg_name,
                            "lines": [],
                            "file": path,
                        }
                    if i not in code_registers[reg_name]["lines"]:
                        code_registers[reg_name]["lines"].append(i)

        result = {
            "document": document_name,
            "code_registers": list(code_registers.values()),
            "modules_scanned": modules_scanned,
        }

        # ── ERP framework fallback ──────────────────────────────
        # Look for ManagerModule to find ERP-style movement definitions
        mgr_modules = [m for m in modules if m.get("module_type") == "ManagerModule"]
        erp_mechanisms: list[str] = []
        manager_tables: list[str] = []
        adapted_registers: list[str] = []

        for mod in mgr_modules:
            mgr_path = mod["path"]
            try:
                mgr_content = read_file_fn(mgr_path)
            except Exception:
                continue

            # ЗарегистрироватьУчетныеМеханизмы → МеханизмыДокумента.Добавить("X")
            mech_body = read_procedure(mgr_path, "ЗарегистрироватьУчетныеМеханизмы")
            if mech_body:
                mech_re = re.compile(r'МеханизмыДокумента\.Добавить\("(\w+)"\)', re.IGNORECASE)
                for m in mech_re.finditer(mech_body):
                    if m.group(1) not in erp_mechanisms:
                        erp_mechanisms.append(m.group(1))

            # ТекстЗапросаТаблицаXxx function names
            table_re = re.compile(r"(?:Функция|Процедура)\s+ТекстЗапросаТаблица(\w+)\s*\(", re.IGNORECASE)
            for m in table_re.finditer(mgr_content):
                table_name = m.group(1)
                if table_name not in manager_tables:
                    manager_tables.append(table_name)

            # АдаптированныйТекстЗапросаДвиженийПоРегистру → ИмяРегистра = "X"
            adapted_body = read_procedure(mgr_path, "АдаптированныйТекстЗапросаДвиженийПоРегистру")
            if adapted_body:
                reg_re = re.compile(r'ИмяРегистра\s*=\s*"(\w+)"', re.IGNORECASE)
                for m in reg_re.finditer(adapted_body):
                    if m.group(1) not in adapted_registers:
                        adapted_registers.append(m.group(1))

        result["erp_mechanisms"] = erp_mechanisms
        result["manager_tables"] = manager_tables
        result["adapted_registers"] = adapted_registers

        return result

    def find_register_writers(register_name: str) -> dict:
        """Find all documents that write to a specific register.
        Searches all document ObjectModules for 'Движения.RegisterName'.

        Args:
            register_name: Register name to search for.

        Returns: dict with register, writers, total_documents_scanned, total_writers."""
        register_name = _strip_meta_prefix(register_name)

        # Fast path: SQLite index
        if idx_reader is not None:
            idx_writers = idx_reader.get_register_writers(register_name)
            if idx_writers is not None:
                return {
                    "register": register_name,
                    "writers": [
                        {"document": w["document_name"], "source": w["source"], "file": w["file"]} for w in idx_writers
                    ],
                    "total_documents_scanned": 0,
                    "total_writers": len(idx_writers),
                }

        _ensure_index()
        # Collect all document ObjectModule files
        doc_modules = [
            (rel, info)
            for rel, info in _index_state
            if info.category and info.category.lower() == "documents" and info.module_type == "ObjectModule"
        ]

        needle = f"движения.{register_name}".lower()
        matched = _parallel_prefilter(doc_modules, needle, base_path)

        movement_re = re.compile(r"Движения\." + re.escape(register_name), re.IGNORECASE)
        writers: list[dict] = []
        for rel, info in matched:
            try:
                content = read_file_fn(rel)
            except Exception:
                continue
            lines: list[int] = []
            for i, line in enumerate(content.splitlines(), 1):
                if movement_re.search(line):
                    lines.append(i)
            if lines:
                writers.append(
                    {
                        "document": info.object_name or "",
                        "file": rel,
                        "lines": lines,
                    }
                )

        return {
            "register": register_name,
            "writers": writers,
            "total_documents_scanned": len(doc_modules),
            "total_writers": len(writers),
        }

    def analyze_document_flow(document_name: str) -> dict:
        """Full document lifecycle analysis: metadata, event subscriptions,
        register movements, and related scheduled jobs.

        Args:
            document_name: Document name (or fragment).

        Returns: dict with document, metadata, event_subscriptions,
                 register_movements, related_scheduled_jobs."""
        document_name = _strip_meta_prefix(document_name)
        obj = analyze_object(document_name)
        subs = find_event_subscriptions(document_name)
        movements = find_register_movements(document_name)

        # Find scheduled jobs referencing this document
        all_jobs = find_scheduled_jobs()
        doc_lower = document_name.lower()
        related_jobs = [
            j
            for j in all_jobs
            if doc_lower in j.get("method_name", "").lower() or doc_lower in j.get("name", "").lower()
        ]

        return {
            "document": obj.get("name", document_name),
            "metadata": obj.get("metadata", {}),
            "event_subscriptions": subs,
            "register_movements": movements,
            "related_scheduled_jobs": related_jobs,
        }

    # ── Based-on documents / Print forms helpers ───────────────

    def find_based_on_documents(document_name: str) -> dict:
        """Find what documents can be created FROM this document and what it can be created FROM.

        Parses ДобавитьКомандыСозданияНаОсновании in ManagerModule and
        ОбработкаЗаполнения in ObjectModule.

        Returns: dict with document, can_create_from_here, can_be_created_from."""
        document_name = _strip_meta_prefix(document_name)
        result: dict = {
            "document": document_name,
            "can_create_from_here": [],
            "can_be_created_from": [],
        }

        modules = find_by_type("Documents", document_name)

        # --- ManagerModule: ДобавитьКомандыСозданияНаОсновании ---
        mgr_modules = [m for m in modules if m.get("module_type") == "ManagerModule"]
        for mod in mgr_modules:
            path = mod["path"]
            body = read_procedure(path, "ДобавитьКомандыСозданияНаОсновании")
            if body:
                create_re = re.compile(r"Документы\.(\w+)\.ДобавитьКоманду\w*НаОснован", re.IGNORECASE)
                for m in create_re.finditer(body):
                    result["can_create_from_here"].append(
                        {
                            "document": m.group(1),
                            "file": path,
                        }
                    )

        # --- ObjectModule: ОбработкаЗаполнения ---
        obj_modules = [m for m in modules if m.get("module_type") == "ObjectModule"]
        for mod in obj_modules:
            path = mod["path"]
            body = read_procedure(path, "ОбработкаЗаполнения")
            if body:
                type_re = re.compile(r'Тип\("(\w+Ссылка\.\w+)"\)', re.IGNORECASE)
                for m in type_re.finditer(body):
                    result["can_be_created_from"].append(
                        {
                            "type": m.group(1),
                            "file": path,
                        }
                    )

        return result

    def find_print_forms(object_name: str) -> dict:
        """Find print forms registered for an object by parsing ДобавитьКомандыПечати in ManagerModule.

        Returns: dict with object, print_forms list."""
        object_name = _strip_meta_prefix(object_name)
        result: dict = {
            "object": object_name,
            "print_forms": [],
        }

        modules = find_by_type("Documents", object_name)
        mgr_modules = [m for m in modules if m.get("module_type") == "ManagerModule"]
        if not mgr_modules:
            # Try broader search (Catalogs, DataProcessors, etc.)
            modules = find_module(object_name)
            mgr_modules = [m for m in modules if m.get("module_type") == "ManagerModule"]

        for mod in mgr_modules:
            path = mod["path"]
            body = read_procedure(path, "ДобавитьКомандыПечати")
            if body:
                # Pattern 1: helper-function style (ERP 1.x / UPP)
                #   ДобавитьКомандуПечати(КомандыПечати, "Ид", НСтр("ru = 'Представление'"))
                print_re = re.compile(
                    r'ДобавитьКомандуПечати\([^,]+,\s*"(\w+)"(?:,\s*НСтр\("ru\s*=\s*\'([^\']+)\')?',
                    re.IGNORECASE,
                )
                for m in print_re.finditer(body):
                    result["print_forms"].append(
                        {
                            "name": m.group(1),
                            "presentation": m.group(2) or "",
                            "file": path,
                        }
                    )

                # Pattern 2: property-style (ERP 2.x)
                #   КомандаПечати.Идентификатор = "Ид";
                #   КомандаПечати.Представление = НСтр("ru = 'Текст'");
                seen_ids = {pf["name"] for pf in result["print_forms"]}
                id_re = re.compile(
                    r'КомандаПечати\.Идентификатор\s*=\s*"(\w+)"',
                    re.IGNORECASE,
                )
                pres_re = re.compile(
                    r"КомандаПечати\.Представление\s*=\s*НСтр\(\"ru\s*=\s*'([^']+)'",
                    re.IGNORECASE,
                )
                ids = id_re.findall(body)
                presentations = pres_re.findall(body)
                for i, name in enumerate(ids):
                    if name not in seen_ids:
                        result["print_forms"].append(
                            {
                                "name": name,
                                "presentation": presentations[i] if i < len(presentations) else "",
                                "file": path,
                            }
                        )
                        seen_ids.add(name)

        return result

    # ── Form XML parsing helper ──────────────────────────────────

    def parse_form(object_name: str, form_name: str = "", handler: str = "") -> list[dict]:
        """Form event handlers, commands and attributes for an object's forms.

        Without form_name — all forms of the object. With form_name — specific form.
        handler='ProcName' — reverse lookup: find what a BSL procedure is bound to.

        Returns: list of dicts grouped by form, each with:
            category, object_name, form_name, file, module_path,
            handlers, commands, attributes."""
        object_name = _strip_meta_prefix(object_name)
        if not object_name:
            raise ValueError("object_name is required, e.g. parse_form('РеализацияТоваровУслуг')")

        # --- Fast path: SQLite index ---
        if idx_reader is not None:
            # Query ALL rows for the object/form (no handler filter at SQL level).
            # handler filters the SET of forms in _group_form_rows, but inside
            # each form commands/attributes stay complete for context.
            raw = idx_reader.get_form_elements(object_name, form_name)
            if raw is not None and raw:
                return _group_form_rows(raw, handler)
            # raw == [] means table exists but no rows — fall through to live
            # path so that empty forms (zero elements) are still discoverable.

        # --- Fallback: path-heuristic discovery ---
        from rlm_tools_bsl.bsl_xml_parsers import parse_form_xml as _parse_form_xml

        form_files: list[tuple[str, str, str, str]] = []  # (cat, obj, frm, rel_path)

        # Check CommonForms first (object_name = form_name)
        for pattern in (
            f"CommonForms/{object_name}/Form.form",
            f"CommonForms/{object_name}/Ext/Form.xml",
        ):
            found = glob_files_fn(pattern)
            for fp in found:
                form_files.append(("CommonForms", object_name, object_name, fp))

        # Standard categories
        from rlm_tools_bsl.format_detector import METADATA_CATEGORIES

        for cat in METADATA_CATEGORIES:
            if cat in ("CommonForms", "CommonModules", "CommonCommands", "CommonTemplates"):
                continue
            for pattern in (
                f"{cat}/{object_name}/Forms/*/Form.form",
                f"{cat}/{object_name}/Forms/*/Ext/Form.xml",
            ):
                found = glob_files_fn(pattern)
                for fp in found:
                    parts = fp.replace("\\", "/").split("/")
                    try:
                        fi = parts.index("Forms")
                        frm = parts[fi + 1]
                    except (ValueError, IndexError):
                        frm = ""
                    form_files.append((cat, object_name, frm, fp))

        # Last resort: broad glob
        if not form_files:
            for pattern in ("**/Forms/*/Form.form", "**/Forms/*/Ext/Form.xml"):
                found = glob_files_fn(pattern)
                for fp in found:
                    if object_name.lower() in fp.lower():
                        parts = fp.replace("\\", "/").split("/")
                        try:
                            fi = parts.index("Forms")
                            frm = parts[fi + 1]
                            obj = parts[fi - 1] if fi > 0 else object_name
                            c = parts[fi - 2] if fi > 1 else ""
                        except (ValueError, IndexError):
                            frm, obj, c = "", object_name, ""
                        form_files.append((c, obj, frm, fp))

        if form_name:
            form_files = [(c, o, f, p) for c, o, f, p in form_files if f == form_name]

        results: list[dict] = []
        for cat, obj, frm, fp in form_files:
            content = read_file_fn(fp)
            if not content:
                continue
            parsed = _parse_form_xml(content)
            if parsed is None:
                continue

            rel = fp if not os.path.isabs(fp) else os.path.relpath(fp, base_path).replace("\\", "/")
            full_fp = fp if os.path.isabs(fp) else os.path.join(base_path, fp)

            # Determine module_path
            module_path = ""
            if full_fp.replace("\\", "/").endswith("Ext/Form.xml"):
                # CF: Ext/Form.xml → module at Ext/Form/Module.bsl
                form_dir = os.path.dirname(full_fp)
                _candidates: tuple[str, ...] = ("Form/Module.bsl", "Module.bsl")
            else:
                form_dir = os.path.dirname(full_fp)
                _candidates = ("Ext/Module.bsl", "Module.bsl")
            for candidate in _candidates:
                mp = os.path.join(form_dir, candidate)
                if os.path.isfile(mp):
                    module_path = os.path.relpath(mp, base_path).replace("\\", "/")
                    break

            hs = parsed.get("handlers", [])
            if handler:
                hs = [h for h in hs if h["handler"].lower() == handler.lower()]
                if not hs:
                    continue

            results.append(
                {
                    "category": cat,
                    "object_name": obj,
                    "form_name": frm,
                    "file": rel,
                    "module_path": module_path,
                    "handlers": hs,
                    "commands": parsed.get("commands", []),
                    "attributes": parsed.get("attributes", []),
                }
            )

        return results

    def _group_form_rows(raw_rows: list[dict], handler_filter: str = "") -> list[dict]:
        """Group raw form_elements rows into per-form dicts."""
        forms: dict[tuple[str, str, str], dict] = {}
        for r in raw_rows:
            key = (r["category"], r["object_name"], r["form_name"])
            if key not in forms:
                # Derive module_path from file path
                file_path = r.get("file", "")
                module_path = ""
                if file_path:
                    if file_path.endswith("Form.form"):
                        # EDT: Form.form → Module.bsl in same dir
                        mp = file_path.rsplit("/", 1)[0] + "/Module.bsl"
                    elif file_path.endswith("Form.xml"):
                        # CF: Ext/Form.xml → Ext/Form/Module.bsl
                        mp = file_path.rsplit("/", 1)[0] + "/Form/Module.bsl"
                    else:
                        mp = ""
                    # Check if exists via glob
                    if mp:
                        found = glob_files_fn(mp)
                        module_path = mp if found else ""

                forms[key] = {
                    "category": r["category"],
                    "object_name": r["object_name"],
                    "form_name": r["form_name"],
                    "file": file_path,
                    "module_path": module_path,
                    "handlers": [],
                    "commands": [],
                    "attributes": [],
                }

            form = forms[key]
            kind = r.get("kind", "")
            if kind == "handler":
                h = {
                    "element": r.get("element_name", ""),
                    "event": r.get("event", ""),
                    "handler": r.get("handler", ""),
                    "element_type": r.get("element_type", ""),
                    "data_path": r.get("data_path", ""),
                    "scope": r.get("scope", ""),
                }
                if handler_filter:
                    if h["handler"].lower() == handler_filter.lower():
                        form["handlers"].append(h)
                else:
                    form["handlers"].append(h)
            elif kind == "command":
                form["commands"].append(
                    {
                        "name": r.get("element_name", ""),
                        "action": r.get("handler", ""),
                    }
                )
            elif kind == "attribute":
                attr: dict = {
                    "name": r.get("element_name", ""),
                    "types": r.get("element_type", ""),
                    "main": bool(r.get("attribute_is_main", 0)),
                }
                mt = r.get("main_table", "")
                if mt:
                    attr["main_table"] = mt
                extra = r.get("extra_json", "")
                if extra:
                    try:
                        ex = json.loads(extra)
                        qt = ex.get("query_text", "")
                        if qt:
                            attr["query_text"] = qt
                    except (json.JSONDecodeError, TypeError):
                        pass
                form["attributes"].append(attr)

        # Filter out forms with no matching handlers when handler_filter is set
        result = list(forms.values())
        if handler_filter:
            result = [f for f in result if f["handlers"]]
        return result

    # ── Enum / FunctionalOption / Roles helpers ──────────────────

    def find_enum_values(enum_name: str) -> dict:
        """Find an enumeration by name and return its values.

        Args:
            enum_name: Enum name (or fragment).

        Returns: dict with name, synonym, values, file — or error."""
        enum_name = _strip_meta_prefix(enum_name)

        # --- Fast path: SQLite index ---
        if idx_reader is not None:
            result = idx_reader.get_enum_values(enum_name)
            if result is not None:
                return result

        # --- Fallback: glob + XML parse ---
        patterns = [
            f"**/Enums/**/*{enum_name}*.xml",
            f"**/Enums/**/*{enum_name}*.mdo",
        ]
        found_files: list[str] = []
        for p in patterns:
            found_files.extend(glob_files_fn(p))
        found_files = list(dict.fromkeys(found_files))

        for f in found_files:
            try:
                content = read_file_fn(f)
            except Exception:
                continue
            parsed = parse_enum_xml(content)
            if parsed is None:
                continue
            if enum_name.lower() in parsed["name"].lower():
                parsed["file"] = f
                return parsed

        return {"error": f"Перечисление '{enum_name}' не найдено"}

    _fo_lazy = LazyList()

    def _build_functional_options() -> list[dict]:
        files = glob_files_fn("**/FunctionalOptions/**/*.xml")
        files.extend(glob_files_fn("**/FunctionalOptions/**/*.mdo"))
        files.extend(glob_files_fn("**/FunctionalOptions/*.xml"))
        files.extend(glob_files_fn("**/FunctionalOptions/*.mdo"))
        files = list(dict.fromkeys(files))
        result: list[dict] = []
        for f in files:
            try:
                content = read_file_fn(f)
            except Exception:
                continue
            parsed = parse_functional_option_xml(content)
            if parsed is None:
                continue
            parsed["file"] = f
            result.append(parsed)
        return result

    def _ensure_functional_options() -> list[dict]:
        return _fo_lazy.ensure(_build_functional_options)

    def find_functional_options(object_name: str) -> dict:
        """Find functional options that affect a given object.
        Also greps BSL modules for ПолучитьФункциональнуюОпцию("X") pattern.
        Uses SQLite index for XML options when available.

        Args:
            object_name: Object name to search for in FO content lists.

        Returns: dict with object, xml_options, code_options."""
        object_name = _strip_meta_prefix(object_name)

        # --- Fast path for xml_options: SQLite index ---
        xml_options: list[dict] | None = None
        if idx_reader is not None:
            xml_options = idx_reader.get_functional_options(object_name)

        if xml_options is None:
            all_fo = _ensure_functional_options()
            name_lower = object_name.lower()
            xml_options = []
            for fo in all_fo:
                matched = any(name_lower in c.lower() for c in fo.get("content", []))
                if matched:
                    xml_options.append(dict(fo))

        # Grep for ПолучитьФункциональнуюОпцию in BSL code
        code_options: list[dict] = []
        try:
            grep_results = safe_grep("ПолучитьФункциональнуюОпцию", name_hint=object_name)
            for r in grep_results:
                text = r.get("text", "") or r.get("content", "")
                # Extract option name from ПолучитьФункциональнуюОпцию("OptionName")
                m = re.search(r'ПолучитьФункциональнуюОпцию\(\s*"([^"]+)"', text)
                if m:
                    code_options.append(
                        {
                            "option_name": m.group(1),
                            "file": r.get("file", ""),
                            "line": r.get("line", 0),
                        }
                    )
        except Exception:
            pass

        return {
            "object": object_name,
            "xml_options": xml_options,
            "code_options": code_options,
        }

    def find_roles(object_name: str) -> dict:
        """Find roles that grant rights to a given object.

        Args:
            object_name: Object name substring to filter rights by.

        Returns: dict with object, roles list."""
        object_name = _strip_meta_prefix(object_name)

        # Fast path: SQLite index
        if idx_reader is not None:
            idx_roles = idx_reader.get_roles(object_name)
            if idx_roles is not None:
                return {"object": object_name, "roles": idx_roles}

        # Fallback: glob + XML parse
        patterns = [
            "**/Roles/*/Ext/Rights.xml",
            "**/Roles/*/*.rights",
        ]
        found_files: list[str] = []
        for p in patterns:
            found_files.extend(glob_files_fn(p))
        found_files = list(dict.fromkeys(found_files))

        roles: list[dict] = []
        for f in found_files:
            # Extract role name from path: Roles/RoleName/Ext/Rights.xml
            parts = f.replace("\\", "/").split("/")
            role_name = ""
            for i, part in enumerate(parts):
                if part == "Roles" and i + 1 < len(parts):
                    role_name = parts[i + 1]
                    break

            try:
                content = read_file_fn(f)
            except Exception:
                continue
            rights = parse_rights_xml(content, object_name)
            for r in rights:
                roles.append(
                    {
                        "role_name": role_name,
                        "object": r["object"],
                        "rights": r["rights"],
                        "file": f,
                    }
                )

        # Group by role_name, merge rights (match index behavior)
        grouped: dict[str, dict] = {}
        for r in roles:
            key = r["role_name"]
            if key not in grouped:
                grouped[key] = {
                    "role_name": key,
                    "object": object_name,
                    "rights": [],
                    "file": r["file"],
                }
            for right in r["rights"]:
                if right not in grouped[key]["rights"]:
                    grouped[key]["rights"].append(right)

        return {"object": object_name, "roles": list(grouped.values())}

    # ── FTS search (requires SQLite index with FTS5) ────────────

    def search_methods(query: str, limit: int = 30) -> list[dict]:
        """Full-text search for methods by name substring (FTS5 trigram).
        Requires a pre-built SQLite index with FTS enabled.

        Args:
            query: Search substring (e.g. 'Провед', 'ОбработкаЗаполнения').
            limit: Max results (default 30).

        Returns: list of dicts {name, type, is_export, line, end_line, params,
                 module_path, object_name, rank} ordered by relevance.
                 Empty list if index/FTS not available."""
        if idx_reader is not None and idx_reader.has_fts:
            return idx_reader.search_methods(query, limit)
        return []

    def search_objects(query: str = "", limit: int = 50) -> list[dict]:
        """Search 1C objects by business name (Russian synonym) or technical name.
        Uses pre-built SQLite index with object synonyms.

        Args:
            query: Search string (e.g. 'себестоимость', 'Авансовый', 'общий модуль').
            limit: Max results (default 50).

        Returns: list of dicts {object_name, category, synonym, file}.
                 Empty list if index not available or no synonyms built."""
        if idx_reader is not None:
            result = idx_reader.search_objects(query, limit)
            if result is not None:
                return result
        return []

    def search_regions(query: str = "", limit: int = 200) -> list[dict]:
        """Search code regions (#Область/#Region) by name substring.

        Args:
            query: Search string (e.g. 'Себестоимость', 'Инициализация').
            limit: Max results (default 200).

        Returns: list of dicts {name, line, end_line, module_path, object_name, category}.
                 Empty list if index not available or no regions built."""
        if idx_reader is not None:
            result = idx_reader.search_regions(query, limit)
            if result is not None:
                return result
        return []

    def search_module_headers(query: str = "", limit: int = 200) -> list[dict]:
        """Search module header comments by substring.

        Args:
            query: Search string (e.g. 'себестоимость', 'доработка').
            limit: Max results (default 200).

        Returns: list of dicts {module_path, object_name, category, header_comment}.
                 Empty list if index not available or no headers built."""
        if idx_reader is not None:
            result = idx_reader.search_module_headers(query, limit)
            if result is not None:
                return result
        return []

    _VALID_SCOPES = frozenset({"all", "methods", "objects", "regions", "headers"})

    def search(query: str, scope: str = "all", limit: int = 30) -> list[dict]:
        """Unified search across methods, object synonyms, regions, headers.

        Args:
            query: Search string (required).
            scope: Filter — 'all', 'methods', 'objects', 'regions', 'headers'.
            limit: Max results (applied to final list).

        Returns: list of dicts {text, source_type, object_name, path, path_kind, detail}.
        """
        if scope not in _VALID_SCOPES:
            msg = f"Unknown scope '{scope}'. Valid: {', '.join(sorted(_VALID_SCOPES))}"
            raise ValueError(msg)

        query = query.strip() if query else ""
        empty_query = not query
        if empty_query and scope == "all":
            return []

        per_source = max(limit // 4, 3) if scope == "all" else limit
        results: list[dict] = []

        if scope in ("all", "methods"):
            if not empty_query:  # search_methods('') → [] by design
                for m in search_methods(query, limit=per_source):
                    results.append(
                        {
                            "text": m["name"],
                            "source_type": "method",
                            "object_name": m.get("object_name", ""),
                            "path": m.get("module_path", ""),
                            "path_kind": "bsl",
                            "detail": m,
                        }
                    )

        if scope in ("all", "objects"):
            raw = search_objects(query, limit=per_source)
            if raw:
                for o in raw:
                    results.append(
                        {
                            "text": o["synonym"],
                            "source_type": "object",
                            "object_name": o.get("object_name", ""),
                            "path": o.get("file", ""),
                            "path_kind": "metadata",
                            "detail": o,
                        }
                    )

        if scope in ("all", "regions"):
            raw = search_regions(query, limit=per_source)
            if raw:
                for r in raw:
                    results.append(
                        {
                            "text": r["name"],
                            "source_type": "region",
                            "object_name": r.get("object_name", ""),
                            "path": r.get("module_path", ""),
                            "path_kind": "bsl",
                            "detail": r,
                        }
                    )

        if scope in ("all", "headers"):
            raw = search_module_headers(query, limit=per_source)
            if raw:
                for h in raw:
                    results.append(
                        {
                            "text": h["header_comment"],
                            "source_type": "header",
                            "object_name": h.get("object_name", ""),
                            "path": h.get("module_path", ""),
                            "path_kind": "bsl",
                            "detail": h,
                        }
                    )

        return results[:limit]

    def get_index_info() -> dict:
        """Return index metadata: version, capabilities, staleness."""
        if idx_reader is None:
            return {"status": "no_index"}
        stats = idx_reader.get_statistics()
        return {
            "status": "ok",
            "builder_version": int(stats.get("builder_version") or 0),
            "config_name": stats.get("config_name", ""),
            "config_version": stats.get("config_version", ""),
            "modules": stats.get("modules", 0),
            "methods": stats.get("methods", 0),
            "has_fts": stats.get("has_fts", False),
            "has_synonyms": bool(stats.get("object_synonyms", 0)),
            "object_synonyms": stats.get("object_synonyms", 0),
            "has_regions": int(stats.get("builder_version") or 0) >= 8,
            "has_module_headers": int(stats.get("builder_version") or 0) >= 8,
            "has_extension_overrides": int(stats.get("builder_version") or 0) >= 9,
            "extension_overrides": stats.get("extension_overrides", 0),
            "has_form_elements": int(stats.get("builder_version") or 0) >= 10 and stats.get("has_metadata", False),
            "form_elements_count": stats.get("form_elements", 0),
            "built_at": stats.get("built_at"),
        }

    # ── Help (uses _registry for recipes) ──────────────────────

    def bsl_help(task: str = "") -> str:
        """Get a recipe for your task. Call help() to see all recipes,
        or help('find exports') / help('граф вызовов') for a specific one.

        Returns: str with Python code example."""
        task_lower = task.lower()

        if not task_lower:
            lines = ["Available recipes (call help('keyword') for details):\n"]
            for name, entry in _registry.items():
                if entry["recipe"]:
                    first_line = entry["recipe"].split("\n")[0]
                    lines.append(f"  help('{name}') - {first_line}")
            return "\n".join(lines)

        # Search by helper name first
        if task_lower in _registry and _registry[task_lower]["recipe"]:
            return _registry[task_lower]["recipe"]

        # Search by keywords
        for name, entry in _registry.items():
            if not entry["recipe"]:
                continue
            if name in task_lower:
                return entry["recipe"]
            for kw in entry["kw"]:
                if kw in task_lower:
                    return entry["recipe"]

        # Fallback: show all recipes
        return bsl_help("")

    # ── Query extraction ───────────────────────────────────────

    _QUERY_ASSIGN_RE = re.compile(
        r'(?:Запрос\.Текст|ТекстЗапроса)\s*=\s*["\']',
        re.IGNORECASE,
    )
    _QUERY_TABLE_RE = re.compile(
        r"\b(?:ИЗ|FROM|СОЕДИНЕНИЕ|JOIN)\s+"
        r"((?:РегистрНакопления|РегистрСведений|РегистрБухгалтерии|"
        r"Справочник|Документ|"
        r"AccumulationRegister|InformationRegister|AccountingRegister|"
        r"Catalog|Document)\.\w+)",
        re.IGNORECASE,
    )

    def extract_queries(path: str) -> list[dict]:
        """Extract embedded 1C queries from a BSL module.

        Finds Запрос.Текст = "..." and ТекстЗапроса = "..." patterns,
        extracts table names from query text.

        Returns: list of dicts {procedure, line, tables: [str], text_preview}."""
        content = read_file_fn(path)
        lines = content.splitlines()
        procs = extract_procedures(path)

        queries: list[dict] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _QUERY_ASSIGN_RE.search(line)
            if not m:
                i += 1
                continue

            # Collect multiline query text (1C uses | prefix for continuation)
            query_start = i
            query_lines = [line[m.end() :]]
            j = i + 1
            while j < len(lines):
                stripped = lines[j].strip()
                if stripped.startswith("|") or stripped.startswith('"'):
                    query_lines.append(stripped.lstrip("|").lstrip('"'))
                elif stripped.startswith("'") or stripped == "":
                    query_lines.append(stripped.lstrip("'"))
                else:
                    break
                j += 1
            query_text = "\n".join(query_lines)

            # Extract table names
            tables = list(dict.fromkeys(m2.group(1) for m2 in _QUERY_TABLE_RE.finditer(query_text)))

            # Determine which procedure this belongs to
            line_num = query_start + 1  # 1-based
            proc_name = ""
            for p in procs:
                if p["line"] <= line_num <= (p["end_line"] or len(lines)):
                    proc_name = p["name"]
                    break

            preview = query_text[:200].strip()
            if len(query_text) > 200:
                preview += "..."

            queries.append(
                {
                    "procedure": proc_name,
                    "line": line_num,
                    "tables": tables,
                    "text_preview": preview,
                }
            )
            i = j
        return queries

    # ── Code metrics ─────────────────────────────────────────

    _COMMENT_RE = re.compile(r"^\s*//")
    _NESTING_OPEN_RE = re.compile(r"\b(Если|Для|Пока|Попытка|If|For|While|Try)\b", re.IGNORECASE)
    _NESTING_CLOSE_RE = re.compile(r"\b(КонецЕсли|КонецЦикла|КонецПопытки|EndIf|EndDo|EndTry)\b", re.IGNORECASE)

    def code_metrics(path: str) -> dict:
        """Compute code metrics for a BSL module.

        Returns: dict {total_lines, code_lines, comment_lines, empty_lines,
                 procedures_count, exports_count, avg_proc_size, max_nesting}."""
        content = read_file_fn(path)
        lines = content.splitlines()

        # Single-pass: empty, comment, nesting depth
        total = len(lines)
        empty = 0
        comment = 0
        max_nesting = 0
        current_nesting = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                empty += 1
            elif _COMMENT_RE.match(line):
                comment += 1
            else:
                for _ in _NESTING_OPEN_RE.finditer(line):
                    current_nesting += 1
                    if current_nesting > max_nesting:
                        max_nesting = current_nesting
                for _ in _NESTING_CLOSE_RE.finditer(line):
                    current_nesting = max(0, current_nesting - 1)
        code = total - empty - comment

        procs = extract_procedures(path)
        exports = [p for p in procs if p.get("is_export")]

        sizes = [(p["end_line"] or total) - p["line"] + 1 for p in procs]
        avg_size = round(sum(sizes) / len(sizes), 1) if sizes else 0

        return {
            "total_lines": total,
            "code_lines": code,
            "comment_lines": comment,
            "empty_lines": empty,
            "procedures_count": len(procs),
            "exports_count": len(exports),
            "avg_proc_size": avg_size,
            "max_nesting": max_nesting,
        }

    # ── Extensions ───────────────────────────────────────────

    def detect_extensions() -> dict:
        """Обнаружить расширения рядом и текущую роль конфигурации."""
        from rlm_tools_bsl.extension_detector import detect_extension_context as _det

        ctx = _det(base_path)
        result = {
            "config_role": ctx.current.role.value,
            "config_name": ctx.current.name,
            "config_prefix": ctx.current.name_prefix,
            "warnings": ctx.warnings,
            "nearby_extensions": [
                {"name": e.name, "purpose": e.purpose, "prefix": e.name_prefix, "path": e.path}
                for e in ctx.nearby_extensions
            ],
            "nearby_main": None,
        }
        if ctx.nearby_main:
            result["nearby_main"] = {
                "name": ctx.nearby_main.name,
                "path": ctx.nearby_main.path,
            }
        return result

    def find_ext_overrides(extension_path: str, object_name: str = "") -> dict:
        """Найти перехваченные методы в расширении.
        extension_path — путь к расширению (из detect_extensions).
        object_name — имя объекта для прицельного поиска ('' = все)."""
        from rlm_tools_bsl.extension_detector import find_extension_overrides as _feo

        overrides = _feo(extension_path, object_name or None)
        return {
            "extension_path": extension_path,
            "object_filter": object_name or "(all)",
            "overrides": overrides[:200],
            "total": len(overrides),
        }

    def get_overrides(object_name: str = "", method_name: str = "") -> dict:
        """Перехваченные методы из индекса (мгновенно).
        object_name/method_name — фильтры ('' = все).
        Возвращает: {overrides: [...], total: N, source: "index"|"live"|"unavailable"}"""
        # Try index first
        if idx_reader is not None:
            result = idx_reader.get_extension_overrides(object_name, method_name)
            if result is not None:
                return {
                    "overrides": result[:200],
                    "total": len(result),
                    "source": "index",
                }
        # Live fallback
        from rlm_tools_bsl.extension_detector import (
            detect_extension_context as _det,
            find_extension_overrides as _feo,
        )

        try:
            ctx = _det(base_path)
        except Exception:
            return {"overrides": [], "total": 0, "source": "unavailable"}

        from rlm_tools_bsl.extension_detector import ConfigRole

        all_overrides: list[dict] = []
        if ctx.current.role == ConfigRole.EXTENSION:
            all_overrides = _feo(base_path, object_name or None)
        elif ctx.current.role == ConfigRole.MAIN and ctx.nearby_extensions:
            for ext in ctx.nearby_extensions:
                try:
                    ovs = _feo(ext.path, object_name or None)
                    for ov in ovs:
                        ov["extension_name"] = ext.name
                        ov["extension_root"] = ext.path
                    all_overrides.extend(ovs)
                except Exception:
                    pass

        if method_name:
            all_overrides = [ov for ov in all_overrides if ov.get("target_method", "").lower() == method_name.lower()]

        return {
            "overrides": all_overrides[:200],
            "total": len(all_overrides),
            "source": "live",
        }

    # ── Register all helpers ─────────────────────────────────────
    # Each _reg() call: name, function, signature (for strategy table),
    # category (for grouping), keywords (for help search), recipe (code example).
    # Adding a new helper = define function above + add _reg() here.

    _reg("find_module", find_module, "find_module(name) -> [{path, category, object_name, module_type}]", "discovery")
    _reg(
        "find_by_type",
        find_by_type,
        "find_by_type(category, name='') -> same. Categories: Documents, Catalogs, CommonModules, InformationRegisters, AccumulationRegisters, Reports, DataProcessors",
        "discovery",
    )

    _reg(
        "extract_procedures",
        extract_procedures,
        "extract_procedures(path) -> [{name, type, line, end_line, is_export, params}]",
        "code",
    )
    _reg(
        "find_exports",
        find_exports,
        "find_exports(path) -> [{name, line, is_export, type, params}]",
        "code",
        ["export", "экспорт", "find_exports", "процедур", "функци"],
        "FIND EXPORTS:\n"
        "  modules = find_module('Name')  # replace 'Name'\n"
        "  path = modules[0]['path']\n"
        "  exports = find_exports(path)\n"
        "  for e in exports:\n"
        "      print(e['name'], 'line:', e['line'], 'export:', e['is_export'])",
    )
    _reg(
        "read_procedure",
        read_procedure,
        "read_procedure(path, proc_name, include_overrides=False) -> str | None",
        "code",
        ["read", "чтени", "читать", "содержим", "content", "тело", "body"],
        "READ PROCEDURE BODY:\n"
        "  body = read_procedure('path/to/Module.bsl', 'ProcedureName')\n"
        "  print(body)\n"
        "  # Or read full file:\n"
        "  content = read_file('path/to/Module.bsl')\n"
        "  print(content[:2000])",
    )
    _reg(
        "find_callers_context",
        find_callers_context,
        "find_callers_context(proc, hint, 0, 50) -> {callers: [{file, caller_name, line, ...}], _meta: {total_callers, returned, offset, has_more}}",
        "code",
        ["caller", "call graph", "граф", "вызов", "вызыва", "кто вызывает", "find_callers"],
        "BUILD CALL GRAPH:\n"
        "  # With index: instant across the whole codebase, hint is optional\n"
        "  # Without index: parallel file scan, hint narrows scope\n"
        "  exports = find_exports('path/to/Module.bsl')\n"
        "  for e in exports:\n"
        "      data = find_callers_context(e['name'], '', 0, 50)\n"
        "      for c in data['callers']:\n"
        "          print(e['name'], '<-', c['caller_name'], c['file'], 'line:', c['line'])\n"
        "      if data['_meta']['has_more']:\n"
        "          print('  ... more callers, increase offset')",
    )
    _reg("find_callers", find_callers, "find_callers(proc, hint, max_files=20) -> [{file, line, text}]", "code")
    _reg(
        "safe_grep",
        safe_grep,
        "safe_grep(pattern, hint, max_files=20) -> [{file, line, text}]",
        "code",
        ["search", "grep", "поиск", "искать", "найти", "pattern", "шаблон"],
        "SEARCH FOR CODE:\n"
        "  results = safe_grep('SearchPattern', 'ModuleHint', max_files=20)\n"
        "  for r in results:\n"
        "      print(r['file'], 'line:', r['line'], r['text'])\n"
        "  # Or find modules by name:\n"
        "  modules = find_module('PartOfName')\n"
        "  for m in modules:\n"
        "      print(m['path'], m['category'], m['object_name'])",
    )

    _reg(
        "parse_object_xml",
        parse_object_xml,
        "parse_object_xml(path) -> {name, synonym, attributes, tabular_sections, dimensions, resources, ...}",
        "xml",
        [
            "metadata",
            "метаданн",
            "реквизит",
            "attribute",
            "dimension",
            "измерен",
            "ресурс",
            "resource",
            "табличн",
            "tabular",
            "xml",
            "parse_object",
        ],
        "READ METADATA:\n"
        "  # Accepts directory or XML path — auto-resolves:\n"
        "  meta = parse_object_xml('Documents/РеализацияТоваровУслуг')  # directory\n"
        "  meta = parse_object_xml('Documents/Name/Ext/Document.xml')   # direct XML\n"
        "  for key in meta:\n"
        "      print(key, ':', meta[key])",
    )
    _reg(
        "parse_form",
        parse_form,
        "parse_form(object_name, form_name='', handler='') -> [{form_name, module_path, handlers, commands, attributes}]",
        "xml",
        kw=["parse_form", "события формы", "обработчики формы", "элементы формы", "form handler", "form event"],
        recipe=(
            "# Обработчики и команды формы объекта:\n"
            "forms = parse_form('БанковскиеСчетаОрганизаций')\n"
            "for f in forms:\n"
            '    print(f\'{f["form_name"]}: {len(f["handlers"])} handlers, {len(f["commands"])} commands\')\n'
            "    for h in f['handlers']:\n"
            '        print(f\'  {h["element"] or "[form]"}.{h["event"]} → {h["handler"]}\')\n\n'
            "# Обратный поиск: к чему привязана процедура?\n"
            "forms = parse_form('БанковскиеСчетаОрганизаций', handler='ПриСозданииНаСервере')\n\n"
            "# module_path для быстрого перехода к коду:\n"
            "for f in forms:\n"
            "    if f['module_path']:\n"
            "        procs = extract_procedures(f['module_path'])\n"
            "        print(f'{f[\"form_name\"]}: {len(procs)} procedures')\n"
        ),
    )
    _reg(
        "find_enum_values",
        find_enum_values,
        "find_enum_values(enum_name) -> {name, synonym, values: [{name, synonym}]}",
        "xml",
        ["перечислен", "enum", "значени перечислени"],
        "FIND ENUM VALUES:\n"
        "  result = find_enum_values('СтатусыЗаказовКлиентов')\n"
        "  print(f\"{result['name']} ({result['synonym']})\")\n"
        "  for v in result['values']:\n"
        "      print(f\"  {v['name']}: {v['synonym']}\")",
    )

    _reg(
        "analyze_object",
        analyze_object,
        "analyze_object(name) -> full profile: metadata + modules + procedures + exports",
        "composite",
        ["profile", "профиль", "обзор", "overview", "analyze_object"],
        "OBJECT PROFILE:\n"
        "  result = analyze_object('АвансовыйОтчет')\n"
        "  meta = result.get('metadata', {})\n"
        "  print(f\"Объект: {result['name']} ({meta.get('synonym', '')})\")\n"
        "  print(f\"Реквизитов: {len(meta.get('attributes', []))}\")\n"
        "  for m in result.get('modules', []):\n"
        "      print(f\"  {m['module_type']}: {m['procedures_count']} проц, {m['exports_count']} эксп\")",
    )
    _reg(
        "analyze_document_flow",
        analyze_document_flow,
        "analyze_document_flow(doc_name) -> metadata + subscriptions + register movements + jobs",
        "composite",
        ["lifecycle", "жизненн", "flow", "end-to-end", "полный анализ", "как работает"],
        "FULL DOCUMENT LIFECYCLE:\n"
        "  flow = analyze_document_flow('АвансовыйОтчет')\n"
        "  print('Подписки:', len(flow['event_subscriptions']))\n"
        "  for s in flow['event_subscriptions']:\n"
        "      print(f\"  {s['event']}: {s['handler']}\")\n"
        "  regs = flow['register_movements'].get('code_registers', [])\n"
        "  print('Регистры:', len(regs))\n"
        "  for r in regs:\n"
        "      print(f\"  Движения.{r['name']}\")",
    )
    _reg(
        "analyze_subsystem",
        analyze_subsystem,
        "analyze_subsystem(name) -> composition, custom vs standard objects",
        "composite",
        ["subsystem", "подсистем", "состав подсистем"],
        "ANALYZE SUBSYSTEM:\n"
        "  result = analyze_subsystem('Спецодежда')\n"
        "  for sub in result.get('subsystems', []):\n"
        "      print(f\"Подсистема: {sub['name']} ({sub['synonym']})\")\n"
        "      print(f\"Нетиповых: {len(sub['custom_objects'])}, типовых: {len(sub['standard_objects'])}\")\n"
        "      for obj in sub['custom_objects']:\n"
        "          print(f\"  [нетип] {obj['type']}.{obj['name']}\")\n"
        "      for obj in sub['standard_objects']:\n"
        "          print(f\"  [типов] {obj['type']}.{obj['name']}\")",
    )
    _reg(
        "find_custom_modifications",
        find_custom_modifications,
        "find_custom_modifications(obj, custom_prefixes=None) -> custom procedures, regions, attributes",
        "composite",
        ["custom", "нетипов", "доработк", "модификац", "modification"],
        "FIND CUSTOM MODIFICATIONS:\n"
        "  result = find_custom_modifications('ВнутреннееПотребление')\n"
        "  for mod in result.get('modifications', []):\n"
        "      print(f\"Модуль: {mod['path']}\")\n"
        "      for p in mod['custom_procedures']:\n"
        "          print(f\"  {p['type']} {p['name']} (стр.{p['line']})\")\n"
        "      for r in mod['custom_regions']:\n"
        "          print(f\"  #Область {r['name']} (стр.{r['line']})\")\n"
        "  for attr in result.get('custom_attributes', []):\n"
        "      print(f\"Реквизит: {attr['name']} ({attr.get('synonym', '')})\")",
    )

    _reg(
        "find_event_subscriptions",
        find_event_subscriptions,
        "find_event_subscriptions(obj, custom_only=False) -> [{event, handler, handler_module, handler_procedure, ...}]",
        "business",
        ["подписк", "subscription", "событи", "event", "BeforeWrite", "OnWrite", "ПриЗаписи", "ПередЗаписью"],
        "FIND EVENT SUBSCRIPTIONS (what fires on document write/post):\n"
        "  # With index: instant. Without: parses XML on first call.\n"
        "  subs = find_event_subscriptions('АвансовыйОтчет')\n"
        "  for s in subs:\n"
        "      print(f\"{s['event']}: {s['handler']} ({s['name']})\")",
    )
    _reg(
        "find_scheduled_jobs",
        find_scheduled_jobs,
        "find_scheduled_jobs(name='') -> [{name, method_name, use, ...}]",
        "business",
        ["регламент", "schedule", "job", "задани", "фонов", "background"],
        "FIND SCHEDULED JOBS:\n"
        "  # With index: instant. Without: parses XML on first call.\n"
        "  jobs = find_scheduled_jobs('Курс')\n"
        "  for j in jobs:\n"
        "      print(f\"{j['name']}: {j['method_name']} (active={j['use']})\")",
    )
    _reg(
        "find_register_movements",
        find_register_movements,
        "find_register_movements(doc_name) -> {code_registers, erp_mechanisms, manager_tables, adapted_registers}",
        "business",
        ["движени", "movement", "регистр", "register", "проведен", "posting"],
        "TRACE DOCUMENT REGISTER MOVEMENTS:\n"
        "  result = find_register_movements('ПриобретениеТоваровУслуг')\n"
        "  for r in result['code_registers']:\n"
        "      detail = r.get('lines') or r.get('source', '')\n"
        "      print(f\"  Движения.{r['name']} ({detail})\")\n"
        "\n"
        "FIND WHO WRITES TO REGISTER:\n"
        "  result = find_register_writers('ТоварыНаСкладах')\n"
        "  for w in result['writers']:\n"
        "      detail = w.get('lines') or w.get('source', '')\n"
        "      print(f\"  {w['document']} ({detail})\")",
    )
    _reg(
        "find_register_writers",
        find_register_writers,
        "find_register_writers(reg_name) -> {writers: [{document, source|lines, file}]}",
        "business",
    )
    _reg(
        "find_based_on_documents",
        find_based_on_documents,
        "find_based_on_documents(doc_name) -> {can_create_from_here, can_be_created_from}",
        "business",
        ["основани", "ввод на основании", "создать на основании", "based on", "filling", "заполнени"],
        "FIND BASED-ON DOCUMENTS (ввод на основании):\n"
        "  result = find_based_on_documents('ПриобретениеТоваровУслуг')\n"
        "  print('Можно создать из этого документа:')\n"
        "  for d in result['can_create_from_here']:\n"
        "      print(f\"  -> {d['document']}\")\n"
        "  print('Этот документ создается на основании:')\n"
        "  for d in result['can_be_created_from']:\n"
        "      print(f\"  <- {d['type']}\")",
    )
    _reg(
        "find_print_forms",
        find_print_forms,
        "find_print_forms(obj_name) -> {print_forms: [{name, presentation}]}",
        "business",
        ["печат", "print", "макет", "template", "накладн"],
        "FIND PRINT FORMS:\n"
        "  result = find_print_forms('РеализацияТоваровУслуг')\n"
        "  for p in result['print_forms']:\n"
        "      print(f\"  {p['name']}: {p['presentation']}\")",
    )
    _reg(
        "find_functional_options",
        find_functional_options,
        "find_functional_options(obj_name) -> {xml_options, code_options}",
        "business",
        ["функциональн", "опци", "functional", "option", "включен", "выключен"],
        "FIND FUNCTIONAL OPTIONS:\n"
        "  # With index: XML options instant. Code grep still runs live.\n"
        "  result = find_functional_options('РеализацияТоваровУслуг')\n"
        "  for fo in result['xml_options']:\n"
        "      print(f\"  {fo['name']}: {fo['synonym']}\")\n"
        "  for co in result['code_options']:\n"
        "      print(f\"  В коде: {co['option_name']} (стр.{co['line']})\")",
    )
    _reg(
        "find_roles",
        find_roles,
        "find_roles(obj_name) -> {roles: [{role_name, rights}]}",
        "business",
        ["роль", "role", "прав", "right", "доступ", "access", "разрешен"],
        "FIND ROLES AND RIGHTS:\n"
        "  result = find_roles('ПриобретениеТоваровУслуг')\n"
        "  for r in result['roles']:\n"
        "      print(f\"  {r['role_name']}: {', '.join(r['rights'])}\")",
    )

    _reg(
        "extract_queries",
        extract_queries,
        "extract_queries(path) -> [{procedure, line, tables, text_preview}]",
        "code",
        ["запрос", "query", "таблиц", "table", "select", "выбрать"],
        "EXTRACT QUERIES FROM MODULE:\n"
        "  queries = extract_queries('path/to/ObjectModule.bsl')\n"
        "  for q in queries:\n"
        "      print(f\"  {q['procedure']} стр.{q['line']}: таблицы={q['tables']}\")\n"
        "      print(f\"    {q['text_preview'][:100]}\")",
    )
    _reg(
        "code_metrics",
        code_metrics,
        "code_metrics(path) -> {total_lines, code_lines, comment_lines, procedures_count, avg_proc_size, max_nesting}",
        "code",
        ["метрик", "metric", "размер", "size", "complex", "сложност", "статистик", "statistic"],
        "CODE METRICS:\n"
        "  m = code_metrics('path/to/Module.bsl')\n"
        "  print(f\"Строк: {m['total_lines']} (код: {m['code_lines']}, комментарии: {m['comment_lines']})\")\n"
        "  print(f\"Процедур: {m['procedures_count']}, экспортных: {m['exports_count']}\")\n"
        "  print(f\"Средний размер: {m['avg_proc_size']} строк, макс. вложенность: {m['max_nesting']}\")",
    )

    _reg(
        "search_methods",
        search_methods,
        "search_methods(query, limit=30) -> [{name, type, is_export, module_path, object_name, rank}]",
        "discovery",
        ["поиск метод", "search", "fts", "full-text", "найти метод", "подстрок"],
        "SEARCH METHODS BY NAME (FTS5, requires pre-built index with --no-fts NOT set):\n"
        "  # Find methods by substring across the entire codebase — instant\n"
        "  results = search_methods('ОбработкаЗаполнения')\n"
        "  for r in results:\n"
        "      print(f\"  {r['name']} ({r['type']}) export={r['is_export']} in {r['module_path']}\")\n"
        "  # Returns [] if index or FTS not available\n"
        "  # Combine with read_procedure() to read found methods:\n"
        "  #   body = read_procedure(r['module_path'], r['name'])",
    )
    _reg(
        "search_objects",
        search_objects,
        "search_objects(query) -> [{object_name, category, synonym, file}] — find by BUSINESS NAME",
        "discovery",
        ["synonym", "синоним", "бизнес", "search_objects", "объект", "business"],
        "SEARCH BY BUSINESS NAME (requires index v7+):\n"
        "  results = search_objects('себестоимость')\n"
        "  for r in results:\n"
        "      print(r['synonym'], r['category'], r['object_name'])",
    )
    _reg(
        "search_regions",
        search_regions,
        "search_regions(query, limit=200) -> [{name, line, end_line, module_path, object_name, category}]",
        "discovery",
        ["область", "region", "search_regions", "#Область"],
        "FIND CODE REGIONS:\n"
        "  regions = search_regions('Себестоимость')\n"
        "  for r in regions:\n"
        "      print(r['category'], r['object_name'], r['name'], f'L{r[\"line\"]}-{r[\"end_line\"]}')",
    )
    _reg(
        "search_module_headers",
        search_module_headers,
        "search_module_headers(query, limit=200) -> [{module_path, object_name, category, header_comment}]",
        "discovery",
        ["заголовок", "header", "комментарий", "search_module_headers"],
        "FIND MODULES BY HEADER COMMENT:\n"
        "  headers = search_module_headers('себестоимость')\n"
        "  for h in headers:\n"
        "      print(h['category'], h['object_name'], h['header_comment'][:80])",
    )
    _reg(
        "search",
        search,
        "search(query, scope='all', limit=30) -> [{text, source_type, object_name, path, path_kind, detail}]",
        "discovery",
        ["поиск", "search", "найти", "unified", "discovery", "искать"],
        "UNIFIED SEARCH across methods, synonyms, regions, headers:\n"
        "  # Broad first pass:\n"
        "  results = search('себестоимость')\n"
        "  for r in results:\n"
        "      print(r['source_type'], r['text'], r['path'])\n"
        "  # Filter by scope:\n"
        "  search('себестоимость', scope='methods')   # only code methods\n"
        "  search('себестоимость', scope='objects')    # only 1C objects by synonym\n"
        "  search('себестоимость', scope='regions')    # only #Область\n"
        "  search('себестоимость', scope='headers')    # only module headers\n"
        "  # Browse mode (empty query, specific scope, set limit for full list):\n"
        "  search('', scope='objects', limit=20000)  # browse objects (default limit=30)",
    )
    _reg(
        "get_index_info",
        get_index_info,
        "get_index_info() -> {builder_version, config_name, has_fts, has_synonyms, ...}",
        "discovery",
        ["index", "version", "индекс", "версия", "info", "get_index_info"],
        "CHECK INDEX:\n  info = get_index_info()\n  print(info['builder_version'], info['has_synonyms'])",
    )

    _reg(
        "find_http_services",
        find_http_services,
        "find_http_services(name='') -> [{name, root_url, templates}]",
        "business",
        ["http", "сервис", "endpoint", "rest", "api"],
        "FIND HTTP SERVICES:\n"
        "  services = find_http_services()\n"
        "  for s in services:\n"
        "      print(f\"  {s['name']} (/{s['root_url']})\")\n"
        "      for t in s['templates']:\n"
        "          print(f\"    {t['template']}: {[m['http_method'] for m in t['methods']]}\")",
    )
    _reg(
        "find_web_services",
        find_web_services,
        "find_web_services(name='') -> [{name, namespace, operations}]",
        "business",
        ["soap", "wsdl", "веб", "web service", "ws"],
        "FIND WEB SERVICES (SOAP):\n"
        "  services = find_web_services()\n"
        "  for s in services:\n"
        "      print(f\"  {s['name']} ns={s['namespace']}\")\n"
        "      for op in s['operations']:\n"
        "          print(f\"    {op['name']}({', '.join(op['params'])}) -> {op['return_type']}\")",
    )
    _reg(
        "find_xdto_packages",
        find_xdto_packages,
        "find_xdto_packages(name='') -> [{name, namespace, types}]",
        "business",
        ["xdto", "пакет", "namespace", "схема", "тип данных"],
        "FIND XDTO PACKAGES:\n"
        "  pkgs = find_xdto_packages()\n"
        "  for p in pkgs:\n"
        "      print(f\"  {p['name']} ns={p['namespace']} types={len(p.get('types', []))}\")",
    )
    _reg(
        "find_exchange_plan_content",
        find_exchange_plan_content,
        "find_exchange_plan_content(name) -> [{ref, auto_record}]",
        "business",
        ["обмен", "exchange", "план обмена", "синхрониз", "регистрац"],
        "FIND EXCHANGE PLAN CONTENT:\n"
        "  content = find_exchange_plan_content('ОбменУправлениеПредприятием')\n"
        "  for item in content:\n"
        "      print(f\"  {item['ref']} auto_record={item['auto_record']}\")",
    )

    _reg(
        "detect_extensions",
        detect_extensions,
        "detect_extensions() -> {config_role, nearby_extensions, nearby_main, warnings}",
        "extension",
    )
    _reg(
        "find_ext_overrides",
        find_ext_overrides,
        "find_ext_overrides(ext_path, obj='') -> {overrides: [{annotation, target_method, extension_method, ...}]}",
        "extension",
    )
    _reg(
        "get_overrides",
        get_overrides,
        "get_overrides(object_name='', method_name='') -> {overrides: [...], total, source}",
        "extension",
        ["перехват", "override", "расширен", "extension", "вместо", "после", "перед"],
        "GET OVERRIDES:\n"
        "  result = get_overrides('Номенклатура')\n"
        "  for ov in result['overrides']:\n"
        "      print(f\"  {ov['target_method']} <- {ov['annotation']} {ov.get('extension_name', '')}\")\n"
        "  # To read extension method body:\n"
        "  body = read_procedure(path, 'MethodName', include_overrides=True)\n"
        "  # NOTE: extension files are outside sandbox — do NOT read them via read_file/glob_files",
    )

    _reg(
        "help",
        bsl_help,
        "help(task='') -> str  # get recipe: help('exports'), help('movements'), help('flow')",
        "navigation",
    )

    # ── Return all helpers (auto-generated from registry) ────────
    return {
        "_detected_prefixes": _ensure_prefixes,
        "_registry": _registry,
        **{k: v["fn"] for k, v in _registry.items()},
    }
