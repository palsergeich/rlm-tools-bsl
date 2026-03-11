from __future__ import annotations
import re
from rlm_tools_bsl.format_detector import parse_bsl_path, BslFileInfo, FormatInfo, METADATA_CATEGORIES
from rlm_tools_bsl.bsl_knowledge import BSL_PATTERNS


def make_bsl_helpers(
    base_path: str,
    resolve_safe,      # callable: str -> pathlib.Path
    read_file_fn,      # callable: str -> str
    grep_fn,           # callable: (pattern, path) -> list[dict]
    glob_files_fn,     # callable: (pattern) -> list[str]
    format_info: FormatInfo | None = None,
) -> dict:
    """Creates BSL helper functions for sandbox namespace.
    Internal _bsl_index is built lazily on first find_module() call."""

    # Mutable closure state for lazy index
    _index_state: list = []          # list of tuples (relative_path, BslFileInfo)
    _index_built: list[bool] = [False]

    def _ensure_index() -> None:
        if _index_built[0]:
            return
        all_bsl = glob_files_fn("**/*.bsl")
        for file_path in all_bsl:
            info = parse_bsl_path(file_path, base_path)
            _index_state.append((info.relative_path, info))
        _index_built[0] = True

    def _info_to_dict(relative_path: str, info: BslFileInfo) -> dict:
        return {
            "path": relative_path,
            "category": info.category,
            "object_name": info.object_name,
            "module_type": info.module_type,
            "form_name": info.form_name,
        }

    def find_module(name: str) -> list[dict]:
        """Find BSL modules by name fragment (case-insensitive)."""
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
        """Find BSL modules by metadata category, optionally filtered by object name."""
        _ensure_index()
        meta_type_lower = meta_type.lower()
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

    def extract_procedures(path: str) -> list[dict]:
        """Parse BSL file and return list of procedures/functions with metadata."""
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

    def find_exports(path: str) -> list[dict]:
        """Return only exported procedures/functions from a BSL file."""
        return [p for p in extract_procedures(path) if p["is_export"]]

    def safe_grep(pattern: str, name_hint: str = "", max_files: int = 20) -> list[dict]:
        """Timeout-safe grep across BSL files, optionally scoped by module name hint."""
        _ensure_index()

        if name_hint:
            candidates = find_module(name_hint)
            paths = [c["path"] for c in candidates[:max_files]]
        else:
            paths = [relative_path for relative_path, _ in _index_state[:max_files]]

        results = []
        for path in paths:
            try:
                matches = grep_fn(pattern, path)
                if matches:
                    results.extend(matches)
            except Exception:
                pass
        return results

    def read_procedure(path: str, proc_name: str) -> str | None:
        """Extract a single procedure body from a BSL file by name."""
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
        return "\n".join(extracted)

    def find_callers(proc_name: str, module_hint: str = "", max_files: int = 20) -> list[dict]:
        """Find all callers of a procedure by name across BSL files."""
        escaped = re.escape(proc_name)
        return safe_grep(escaped, name_hint=module_hint, max_files=max_files)

    return {
        "find_module": find_module,
        "find_by_type": find_by_type,
        "extract_procedures": extract_procedures,
        "find_exports": find_exports,
        "safe_grep": safe_grep,
        "read_procedure": read_procedure,
        "find_callers": find_callers,
    }
