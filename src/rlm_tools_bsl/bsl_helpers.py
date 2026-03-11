from __future__ import annotations
import re
import xml.etree.ElementTree as ET
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

    attributes = _cf_parse_attributes(meta_el, ns)
    if attributes:
        result["attributes"] = attributes

    tab_sections = []
    for ts_el in meta_el.findall("md:TabularSection", ns):
        ts_props = ts_el.find("md:Properties", ns)
        ts_name = _xml_find_text(ts_props, "md:Name", ns) if ts_props is not None else ""
        ts_synonym = _cf_find_synonym(ts_props, ns) if ts_props is not None else ""
        ts_attrs = _cf_parse_attributes(ts_el, ns)
        tab_sections.append({"name": ts_name, "synonym": ts_synonym, "attributes": ts_attrs})
    if tab_sections:
        result["tabular_sections"] = tab_sections

    dimensions = []
    for dim_el in meta_el.findall("md:Dimension", ns):
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
    for res_el in meta_el.findall("md:Resource", ns):
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

        cached = load_index(base_path, bsl_count)
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

    def parse_object_xml(path: str) -> dict:
        """Read a 1C metadata XML file and extract its structure:
        name, synonym, attributes, tabular sections, dimensions, resources,
        subsystem content. Works with any metadata XML (catalogs, documents,
        registers, subsystems, etc.)."""
        content = read_file_fn(path)
        return parse_metadata_xml(content)

    return {
        "find_module": find_module,
        "find_by_type": find_by_type,
        "extract_procedures": extract_procedures,
        "find_exports": find_exports,
        "safe_grep": safe_grep,
        "read_procedure": read_procedure,
        "find_callers": find_callers,
        "parse_object_xml": parse_object_xml,
    }
