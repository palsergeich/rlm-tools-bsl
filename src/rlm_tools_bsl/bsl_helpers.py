from __future__ import annotations
import concurrent.futures
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from rlm_tools_bsl.format_detector import parse_bsl_path, BslFileInfo, FormatInfo, METADATA_CATEGORIES
from rlm_tools_bsl.bsl_knowledge import BSL_PATTERNS
from rlm_tools_bsl.cache import load_index, save_index


# Namespace maps for 1C metadata XML
# CF format (Platform Export / Конфигуратор)
_NS_CF = {
    "md": "http://v8.1c.ru/8.3/MDClasses",
    "v8": "http://v8.1c.ru/8.1/data/core",
    "xr": "http://v8.1c.ru/8.3/xcf/readable",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "cfg": "http://v8.1c.ru/8.1/data/enterprise/current-config",
}

# MDO format (EDT / 1C:DT)
_NS_MDO = {
    "mdclass": "http://g5.1c.ru/v8/dt/metadata/mdclass",
    "mdext": "http://g5.1c.ru/v8/dt/metadata/mdclass/extension",
    "core": "http://g5.1c.ru/v8/dt/mcore",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

_MDO_NS_URI = "http://g5.1c.ru/v8/dt/metadata/mdclass"
_CF_NS_URI = "http://v8.1c.ru/8.3/MDClasses"

# Aliases for metadata categories: singular -> plural, Russian -> English
_CATEGORY_ALIASES: dict[str, str] = {
    # English singular -> plural
    "informationregister": "informationregisters",
    "accumulationregister": "accumulationregisters",
    "accountingregister": "accountingregisters",
    "calculationregister": "calculationregisters",
    "document": "documents",
    "catalog": "catalogs",
    "report": "reports",
    "dataprocessor": "dataprocessors",
    "commonmodule": "commonmodules",
    "constant": "constants",
    # Russian aliases
    "регистрсведений": "informationregisters",
    "регистрнакопления": "accumulationregisters",
    "регистрбухгалтерии": "accountingregisters",
    "регистррасчета": "calculationregisters",
    "документ": "documents",
    "справочник": "catalogs",
    "отчет": "reports",
    "обработка": "dataprocessors",
    "общиймодуль": "commonmodules",
    "константа": "constants",
}


def _normalize_category(meta_type: str) -> str:
    """Normalize a metadata category name to the canonical folder form."""
    key = meta_type.lower().replace(" ", "").replace("_", "")
    resolved = _CATEGORY_ALIASES.get(key)
    if resolved:
        return resolved
    # Fallback: if it doesn't end with 's', try adding 's'
    if not key.endswith("s"):
        candidate = key + "s"
        if candidate in {c.lower() for c in METADATA_CATEGORIES}:
            return candidate
    return key


def _xml_find_text(element, tag: str, ns: dict) -> str:
    """Find text of a child element, return '' if not found."""
    el = element.find(tag, ns)
    return el.text.strip() if el is not None and el.text else ""


def _xml_direct_text(element, child_name: str) -> str:
    """Find direct child by local name, return text."""
    for ch in element:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local == child_name:
            return ch.text.strip() if ch.text else ""
    return ""


# --- CF format helpers ---

def _cf_find_synonym(props, ns: dict = _NS_CF) -> str:
    """Extract ru synonym from CF Properties element."""
    syn = props.find("md:Synonym", ns)
    if syn is None:
        return ""
    for item in syn.findall("v8:item", ns):
        lang = _xml_find_text(item, "v8:lang", ns)
        if lang == "ru":
            return _xml_find_text(item, "v8:content", ns)
    return ""


def _cf_parse_type(props, ns: dict = _NS_CF) -> str:
    """Extract type string from CF <Type> element."""
    type_el = props.find("md:Type", ns)
    if type_el is None:
        return ""
    types = []
    for t in type_el.findall("v8:Type", ns):
        if t.text:
            types.append(t.text.strip())
    return ", ".join(types)


def _cf_parse_attributes(parent, ns: dict = _NS_CF) -> list[dict]:
    """Parse CF <Attribute> elements under parent."""
    attrs = []
    for attr_el in parent.findall("md:Attribute", ns):
        props = attr_el.find("md:Properties", ns)
        if props is None:
            continue
        attrs.append({
            "name": _xml_find_text(props, "md:Name", ns),
            "synonym": _cf_find_synonym(props, ns),
            "type": _cf_parse_type(props, ns),
        })
    return attrs


def _parse_cf_xml(root) -> dict:
    """Parse CF-format metadata XML (Platform Export / Конфигуратор)."""
    ns = _NS_CF

    meta_el = None
    for child in root:
        if child.find("md:Properties", ns) is not None:
            meta_el = child
            break
        if child.find("{http://v8.1c.ru/8.3/MDClasses}Properties") is not None:
            meta_el = child
            break
    if meta_el is None:
        for child in root:
            for sub in child:
                sub_tag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                if sub_tag == "Properties":
                    meta_el = child
                    break
            if meta_el is not None:
                break

    if meta_el is None:
        return {"error": "Could not find metadata object in XML"}

    meta_tag = meta_el.tag.split("}")[-1] if "}" in meta_el.tag else meta_el.tag

    props = meta_el.find("md:Properties", ns)
    if props is None:
        for ch in meta_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break

    result: dict = {
        "object_type": meta_tag,
        "name": _xml_find_text(props, "md:Name", ns) if props is not None else "",
        "synonym": _cf_find_synonym(props, ns) if props is not None else "",
    }

    # In CF format, Attribute/TabularSection/Dimension/Resource elements
    # can be either direct children of meta_el OR inside <ChildObjects>.
    child_objects = meta_el.find("md:ChildObjects", ns)
    search_el = child_objects if child_objects is not None else meta_el

    attributes = _cf_parse_attributes(search_el, ns)
    if attributes:
        result["attributes"] = attributes

    tab_sections = []
    for ts_el in search_el.findall("md:TabularSection", ns):
        ts_props = ts_el.find("md:Properties", ns)
        ts_name = _xml_find_text(ts_props, "md:Name", ns) if ts_props is not None else ""
        ts_synonym = _cf_find_synonym(ts_props, ns) if ts_props is not None else ""
        ts_attrs = _cf_parse_attributes(ts_el, ns)
        tab_sections.append({"name": ts_name, "synonym": ts_synonym, "attributes": ts_attrs})
    if tab_sections:
        result["tabular_sections"] = tab_sections

    dimensions = []
    for dim_el in search_el.findall("md:Dimension", ns):
        dim_props = dim_el.find("md:Properties", ns)
        if dim_props is not None:
            dimensions.append({
                "name": _xml_find_text(dim_props, "md:Name", ns),
                "synonym": _cf_find_synonym(dim_props, ns),
                "type": _cf_parse_type(dim_props, ns),
            })
    if dimensions:
        result["dimensions"] = dimensions

    resources = []
    for res_el in search_el.findall("md:Resource", ns):
        res_props = res_el.find("md:Properties", ns)
        if res_props is not None:
            resources.append({
                "name": _xml_find_text(res_props, "md:Name", ns),
                "synonym": _cf_find_synonym(res_props, ns),
                "type": _cf_parse_type(res_props, ns),
            })
    if resources:
        result["resources"] = resources

    if props is not None:
        content_el = props.find("md:Content", ns)
        if content_el is not None:
            items = []
            for item in content_el.findall("xr:Item", ns):
                if item.text:
                    items.append(item.text.strip())
            if items:
                result["content"] = items

    return result


# --- MDO format helpers ---

def _mdo_find_synonym(element) -> str:
    """Extract ru synonym from MDO element. MDO uses <synonym><key>ru</key><value>...</value></synonym>."""
    for syn in element:
        local = syn.tag.split("}")[-1] if "}" in syn.tag else syn.tag
        if local != "synonym":
            continue
        key = ""
        value = ""
        for ch in syn:
            ch_local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
            if ch_local == "key" and ch.text:
                key = ch.text.strip()
            elif ch_local == "value" and ch.text:
                value = ch.text.strip()
        if key == "ru":
            return value
    return ""


def _mdo_parse_type(element) -> str:
    """Extract type string from MDO element. MDO uses <type><types>...</types></type>."""
    for ch in element:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "type":
            continue
        types = []
        for t in ch:
            t_local = t.tag.split("}")[-1] if "}" in t.tag else t.tag
            if t_local == "types" and t.text:
                types.append(t.text.strip())
        if types:
            return ", ".join(types)
    return ""


def _mdo_parse_attributes(parent) -> list[dict]:
    """Parse MDO <attributes> elements under parent."""
    ns_uri = _MDO_NS_URI
    attrs = []
    for ch in parent:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "attributes":
            continue
        attrs.append({
            "name": _xml_direct_text(ch, "name"),
            "synonym": _mdo_find_synonym(ch),
            "type": _mdo_parse_type(ch),
        })
    return attrs


def _parse_mdo_xml(root) -> dict:
    """Parse MDO-format metadata XML (EDT / 1C:DT)."""
    meta_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    result: dict = {
        "object_type": meta_tag,
        "name": _xml_direct_text(root, "name"),
        "synonym": _mdo_find_synonym(root),
    }

    attributes = _mdo_parse_attributes(root)
    if attributes:
        result["attributes"] = attributes

    # Tabular sections: <tabularSections>
    tab_sections = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "tabularSections":
            continue
        ts_attrs = _mdo_parse_attributes(ch)
        tab_sections.append({
            "name": _xml_direct_text(ch, "name"),
            "synonym": _mdo_find_synonym(ch),
            "attributes": ts_attrs,
        })
    if tab_sections:
        result["tabular_sections"] = tab_sections

    # Dimensions: <dimensions>
    dimensions = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "dimensions":
            continue
        dimensions.append({
            "name": _xml_direct_text(ch, "name"),
            "synonym": _mdo_find_synonym(ch),
            "type": _mdo_parse_type(ch),
        })
    if dimensions:
        result["dimensions"] = dimensions

    # Resources: <resources>
    resources = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "resources":
            continue
        resources.append({
            "name": _xml_direct_text(ch, "name"),
            "synonym": _mdo_find_synonym(ch),
            "type": _mdo_parse_type(ch),
        })
    if resources:
        result["resources"] = resources

    # Subsystem content: <content> direct children with text
    content_items = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local == "content" and ch.text:
            content_items.append(ch.text.strip())
    if content_items:
        result["content"] = content_items

    # Forms, commands, templates — list names
    forms = []
    commands = []
    templates = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local == "forms" and ch.text:
            forms.append(ch.text.strip())
        elif local == "commands" and ch.text:
            commands.append(ch.text.strip())
        elif local == "templates" and ch.text:
            templates.append(ch.text.strip())
    if forms:
        result["forms"] = forms
    if commands:
        result["commands"] = commands
    if templates:
        result["templates"] = templates

    return result


def parse_metadata_xml(xml_content: str) -> dict:
    """Parse 1C metadata XML and extract structure: name, synonym, attributes,
    tabular sections, subsystem content, dimensions, resources, etc.
    Auto-detects format: CF (Platform Export) or MDO (EDT/1C:DT)."""
    root = ET.fromstring(xml_content)

    # Detect format by root tag namespace
    root_ns = ""
    if "}" in root.tag:
        root_ns = root.tag.split("}")[0].lstrip("{")

    if root_ns == _MDO_NS_URI or _MDO_NS_URI in root_ns:
        # MDO format — root IS the metadata object
        return _parse_mdo_xml(root)
    else:
        # CF format — root is <MetaDataObject> wrapper
        return _parse_cf_xml(root)


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

    def _info_to_dict(relative_path: str, info: BslFileInfo) -> dict:
        return {
            "path": relative_path,
            "category": info.category,
            "object_name": info.object_name,
            "module_type": info.module_type,
            "form_name": info.form_name,
        }

    def find_module(name: str) -> list[dict]:
        """Find BSL modules by name fragment (case-insensitive).

        Returns: list of dicts {path, category, object_name, module_type, form_name}."""
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

    _proc_cache: dict[str, list[dict]] = {}
    _prefilter_cache: dict[str, list[tuple[str, BslFileInfo]]] = {}

    def extract_procedures(path: str) -> list[dict]:
        """Parse BSL file and return list of procedures/functions with metadata.
        Results are memoized per file path within the session.

        Returns: list of dicts {name, type, line, end_line, is_export, params}."""
        if path in _proc_cache:
            return _proc_cache[path]

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

        _proc_cache[path] = procedures
        return procedures

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
        """Find all callers of a procedure by name across BSL files.
        Delegates to find_callers_context for thorough cross-module search.

        Returns: list of dicts {file, line, text}."""
        result = find_callers_context(proc_name, module_hint, 0, max_files)
        return [
            {"file": c["file"], "line": c["line"], "text": c.get("context", "")}
            for c in result["callers"]
        ]

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

        Args:
            proc_name: Name of the target procedure/function.
            module_hint: Optional module name to determine export scope.
            offset: File offset for pagination (0-based).
            limit: Max files to scan per call (default 50).

        Returns:
            dict with "callers" list and "_meta" pagination info.
        """
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
            candidate_files = [
                (rel, info)
                for rel, info in _index_state
                if rel in target_files
            ]
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
        elif proc_lower in _prefilter_cache:
            filtered_files = _prefilter_cache[proc_lower]
        else:
            filtered_files = _parallel_prefilter(
                candidate_files, proc_lower, base_path,
            )
            _prefilter_cache[proc_lower] = filtered_files

        total_files = len(filtered_files)

        # --- Step 4: Apply pagination ---
        page_files = filtered_files[offset:offset + limit]
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
                                callers.append({
                                    "file": rel,
                                    "caller_name": proc["name"],
                                    "caller_is_export": proc["is_export"],
                                    "line": line_idx + 1,  # 1-based
                                    "context": raw_line.rstrip(),
                                    "object_name": info.object_name,
                                    "category": info.category,
                                    "module_type": info.module_type,
                                })
                                break  # one match per line is enough
            except Exception:
                pass

        return {
            "callers": callers,
            "_meta": {
                "total_files": total_files,
                "scanned_files": scanned_files,
                "has_more": (offset + limit) < total_files,
            },
        }

    def parse_object_xml(path: str) -> dict:
        """Read a 1C metadata XML file and extract its structure:
        name, synonym, attributes, tabular sections, dimensions, resources,
        subsystem content. Works with any metadata XML (catalogs, documents,
        registers, subsystems, etc.).

        Returns: dict with keys like name, synonym, attributes, tabular_sections,
        dimensions, resources (depends on metadata type)."""
        content = read_file_fn(path)
        return parse_metadata_xml(content)

    # ── Composite helpers (wrappers over existing functions) ────────

    def analyze_subsystem(name: str) -> dict:
        """Find a subsystem by name, parse its XML composition,
        classify objects as custom (non-standard prefix) or standard.

        Returns: dict with subsystems_found, subsystems list."""
        patterns = [
            f"**/Subsystems/**/*{name}*",
            f"**/Subsystems/*{name}*",
            f"**/*{name}*.mdo",
        ]
        found_files: list[str] = []
        for p in patterns:
            found_files.extend(glob_files_fn(p))

        subsystem_files = list(dict.fromkeys(
            f for f in found_files
            if "Subsystem" in f and (f.endswith(".xml") or f.endswith(".mdo"))
        ))

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

            results.append({
                "file": sf,
                "name": meta.get("name", ""),
                "synonym": meta.get("synonym", ""),
                "total_objects": len(content),
                "custom_objects": custom_objects,
                "standard_objects": standard_objects,
                "raw_content": content,
            })

        return {"subsystems_found": len(results), "subsystems": results}

    _CUSTOM_PREFIXES_DEFAULT = ["лтх", "бг", "кэ", "мп"]

    def find_custom_modifications(
        object_name: str,
        custom_prefixes: list[str] | None = None,
    ) -> dict:
        """Find all non-standard (custom) modifications in an object's modules:
        procedures with custom prefix, #Область ИРИС regions, custom XML attributes.

        Returns: dict with modifications list and custom_attributes."""
        prefixes = custom_prefixes or _CUSTOM_PREFIXES_DEFAULT

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
                        if _match_prefix(region_name) or region_name.upper() == "ИРИС":
                            custom_regions.append({"name": region_name, "line": i})
            except Exception:
                pass

            if custom_procs or custom_regions:
                modifications.append({
                    "path": path,
                    "module_type": mod.get("module_type", ""),
                    "form_name": mod.get("form_name"),
                    "total_procedures": len(procs),
                    "custom_procedures": custom_procs,
                    "custom_regions": custom_regions,
                })

        custom_attributes: list[dict] = []
        category = exact[0].get("category", "")
        obj_name = exact[0].get("object_name", "")
        if category and obj_name:
            for xp in [f"{category}/{obj_name}.xml", f"{category}/{obj_name}.mdo", f"{category}/{obj_name}/{obj_name}.mdo"]:
                try:
                    meta = parse_object_xml(xp)
                    for attr in meta.get("attributes", []):
                        if _match_prefix(attr["name"]):
                            custom_attributes.append(attr)
                    for ts in meta.get("tabular_sections", []):
                        if _match_prefix(ts["name"]):
                            custom_attributes.append({
                                "name": ts["name"],
                                "type": "TabularSection",
                                "synonym": ts.get("synonym", ""),
                            })
                    break
                except Exception:
                    continue

        return {
            "object_name": object_name,
            "modules_analyzed": len(exact),
            "modifications": modifications,
            "custom_attributes": custom_attributes,
        }

    def analyze_object(name: str) -> dict:
        """Full object profile in one call: XML metadata + all modules + procedures + exports.

        Returns: dict with name, category, metadata, modules."""
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
            for xp in [f"{category}/{obj_name}.xml", f"{category}/{obj_name}.mdo", f"{category}/{obj_name}/{obj_name}.mdo"]:
                try:
                    metadata = parse_object_xml(xp)
                    break
                except Exception:
                    continue

        module_details = []
        for mod in exact:
            path = mod["path"]
            try:
                procs = extract_procedures(path)
                exports = [p for p in procs if p.get("is_export")]
            except Exception:
                procs, exports = [], []

            module_details.append({
                "path": path,
                "module_type": mod.get("module_type", ""),
                "form_name": mod.get("form_name"),
                "procedures_count": len(procs),
                "exports_count": len(exports),
                "procedures": procs,
                "exports": exports,
            })

        return {
            "name": obj_name,
            "category": category,
            "metadata": metadata,
            "modules": module_details,
        }

    # ── Help recipes ─────────────────────────────────────────────

    _help_recipes: dict[str, dict] = {
        "exports": {
            "keywords": ["export", "экспорт", "find_exports", "процедур", "функци"],
            "text": (
                "FIND EXPORTS:\n"
                "  modules = find_module('Name')  # replace 'Name'\n"
                "  path = modules[0]['path']\n"
                "  exports = find_exports(path)\n"
                "  for e in exports:\n"
                "      print(e['name'], 'line:', e['line'], 'export:', e['is_export'])"
            ),
        },
        "callers": {
            "keywords": ["caller", "call graph", "граф", "вызов", "вызыва",
                         "кто вызывает", "find_callers"],
            "text": (
                "BUILD CALL GRAPH:\n"
                "  exports = find_exports('path/to/Module.bsl')\n"
                "  for e in exports:\n"
                "      data = find_callers_context(e['name'], 'ModuleHint', 0, 20)\n"
                "      for c in data['callers']:\n"
                "          print(e['name'], '<-', c['caller_name'], c['file'], 'line:', c['line'])\n"
                "      if data['_meta']['has_more']:\n"
                "          print('  ... more callers, increase offset')"
            ),
        },
        "metadata": {
            "keywords": ["metadata", "метаданн", "реквизит", "attribute", "dimension",
                         "измерен", "ресурс", "resource", "табличн", "tabular",
                         "xml", "parse_object"],
            "text": (
                "READ METADATA:\n"
                "  # CF XML paths: Catalogs/Name/Ext/Catalog.xml,\n"
                "  #   Documents/Name/Ext/Document.xml,\n"
                "  #   InformationRegisters/Name/Ext/RecordSet.xml\n"
                "  meta = parse_object_xml('path/to/Object.xml')\n"
                "  for key in meta:\n"
                "      print(key, ':', meta[key])"
            ),
        },
        "search": {
            "keywords": ["search", "grep", "поиск", "искать", "найти",
                         "pattern", "шаблон"],
            "text": (
                "SEARCH FOR CODE:\n"
                "  results = safe_grep('SearchPattern', 'ModuleHint', max_files=20)\n"
                "  for r in results:\n"
                "      print(r['file'], 'line:', r['line'], r['text'])\n"
                "  # Or find modules by name:\n"
                "  modules = find_module('PartOfName')\n"
                "  for m in modules:\n"
                "      print(m['path'], m['category'], m['object_name'])"
            ),
        },
        "read": {
            "keywords": ["read", "чтени", "читать", "содержим", "content",
                         "тело", "body"],
            "text": (
                "READ PROCEDURE BODY:\n"
                "  body = read_procedure('path/to/Module.bsl', 'ProcedureName')\n"
                "  print(body)\n"
                "  # Or read full file:\n"
                "  content = read_file('path/to/Module.bsl')\n"
                "  print(content[:2000])"
            ),
        },
        "subsystem": {
            "keywords": ["subsystem", "подсистем", "состав подсистем"],
            "text": (
                "ANALYZE SUBSYSTEM:\n"
                "  result = analyze_subsystem('Спецодежда')\n"
                "  for sub in result.get('subsystems', []):\n"
                "      print(f\"Подсистема: {sub['name']} ({sub['synonym']})\")\n"
                "      print(f\"Нетиповых: {len(sub['custom_objects'])}, типовых: {len(sub['standard_objects'])}\")\n"
                "      for obj in sub['custom_objects']:\n"
                "          print(f\"  [нетип] {obj['type']}.{obj['name']}\")\n"
                "      for obj in sub['standard_objects']:\n"
                "          print(f\"  [типов] {obj['type']}.{obj['name']}\")"
            ),
        },
        "custom": {
            "keywords": ["custom", "нетипов", "доработк", "модификац",
                         "modification", "ИРИС", "ирис"],
            "text": (
                "FIND CUSTOM MODIFICATIONS:\n"
                "  result = find_custom_modifications('ВнутреннееПотребление')\n"
                "  for mod in result.get('modifications', []):\n"
                "      print(f\"Модуль: {mod['path']}\")\n"
                "      for p in mod['custom_procedures']:\n"
                "          print(f\"  {p['type']} {p['name']} (стр.{p['line']})\")\n"
                "      for r in mod['custom_regions']:\n"
                "          print(f\"  #Область {r['name']} (стр.{r['line']})\")\n"
                "  for attr in result.get('custom_attributes', []):\n"
                "      print(f\"Реквизит: {attr['name']} ({attr.get('synonym', '')})\")"
            ),
        },
        "profile": {
            "keywords": ["profile", "профиль", "обзор", "overview",
                         "analyze_object"],
            "text": (
                "OBJECT PROFILE:\n"
                "  result = analyze_object('АвансовыйОтчет')\n"
                "  meta = result.get('metadata', {})\n"
                "  print(f\"Объект: {result['name']} ({meta.get('synonym', '')})\")\n"
                "  print(f\"Реквизитов: {len(meta.get('attributes', []))}\")\n"
                "  for m in result.get('modules', []):\n"
                "      print(f\"  {m['module_type']}: {m['procedures_count']} проц, {m['exports_count']} эксп\")"
            ),
        },
    }

    def bsl_help(task: str = "") -> str:
        """Get a recipe for your task. Call help() to see all recipes,
        or help('find exports') / help('граф вызовов') for a specific one.

        Returns: str with Python code example."""
        task_lower = task.lower()

        if not task_lower:
            lines = ["Available recipes (call help('keyword') for details):\n"]
            for name, recipe in _help_recipes.items():
                first_line = recipe["text"].split("\n")[0]
                lines.append(f"  help('{name}') - {first_line}")
            return "\n".join(lines)

        for name, recipe in _help_recipes.items():
            if name in task_lower:
                return recipe["text"]
            for kw in recipe["keywords"]:
                if kw in task_lower:
                    return recipe["text"]

        # Fallback: show all recipes
        return bsl_help("")

    return {
        "help": bsl_help,
        "find_module": find_module,
        "find_by_type": find_by_type,
        "extract_procedures": extract_procedures,
        "find_exports": find_exports,
        "safe_grep": safe_grep,
        "read_procedure": read_procedure,
        "find_callers": find_callers,
        "find_callers_context": find_callers_context,
        "parse_object_xml": parse_object_xml,
        "analyze_subsystem": analyze_subsystem,
        "find_custom_modifications": find_custom_modifications,
        "analyze_object": analyze_object,
    }
