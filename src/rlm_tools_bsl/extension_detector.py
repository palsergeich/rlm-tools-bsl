"""Detection of 1C configuration extensions and method overrides.

Determines whether a given source directory is a main configuration or an
extension, discovers nearby extensions/main configs, and performs targeted
scanning of BSL files for interception annotations.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from rlm_tools_bsl.format_detector import parse_bsl_path
from rlm_tools_bsl.helpers import _SKIP_DIRS


class ConfigRole(Enum):
    MAIN = "main"
    EXTENSION = "extension"
    UNKNOWN = "unknown"


@dataclass
class ExtensionInfo:
    path: str
    role: ConfigRole
    name: str = ""
    purpose: str = ""  # "AddOn", "Customization", "Fix", ""
    name_prefix: str = ""
    source_format: str = ""  # "cf" or "edt"


@dataclass
class ExtensionContext:
    current: ExtensionInfo
    nearby_extensions: list[ExtensionInfo] = field(default_factory=list)
    nearby_main: ExtensionInfo | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# XML namespace maps (minimal — only what's needed for Configuration root)
# ---------------------------------------------------------------------------
_NS_CF = "http://v8.1c.ru/8.3/MDClasses"
_NS_MDO = "http://g5.1c.ru/v8/dt/metadata/mdclass"

# Annotation regex for BSL extension overrides
_ANNOTATION_RE = re.compile(
    r"&(Перед|После|Вместо|ИзменениеИКонтроль)\s*\(\s*\"([^\"]+)\"\s*\)",
    re.IGNORECASE,
)

# Procedure/function definition following an annotation
_PROC_DEF_RE = re.compile(
    r"^\s*(?:Процедура|Функция|Procedure|Function)\s+(\w+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# CF format (Configuration.xml under default namespace)
# ---------------------------------------------------------------------------


def _parse_config_xml(xml_path: str, directory: str) -> ExtensionInfo | None:
    """Parse CF-format Configuration.xml and determine role."""
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError):
        return None

    root = tree.getroot()
    ns = {"md": _NS_CF}

    # Structure: <MetaDataObject><Configuration><Properties>...
    config_el = root.find("md:Configuration", ns)
    if config_el is None:
        # Try without namespace (shouldn't happen, but be safe)
        config_el = root.find("Configuration")
    if config_el is None:
        return None

    props = config_el.find("md:Properties", ns)
    if props is None:
        props = config_el.find("Properties")
    if props is None:
        return None

    name = _el_text(props, "Name", ns)
    name_prefix = _el_text(props, "NamePrefix", ns)
    ext_purpose = _el_text(props, "ConfigurationExtensionPurpose", ns)

    if ext_purpose:
        role = ConfigRole.EXTENSION
    else:
        role = ConfigRole.MAIN

    return ExtensionInfo(
        path=directory,
        role=role,
        name=name,
        purpose=ext_purpose,
        name_prefix=name_prefix,
        source_format="cf",
    )


def _el_text(parent, tag: str, ns: dict) -> str:
    """Get text of a child element (try with and without namespace)."""
    el = parent.find(f"md:{tag}", ns)
    if el is None:
        el = parent.find(tag)
    if el is not None and el.text:
        return el.text.strip()
    return ""


# ---------------------------------------------------------------------------
# EDT format (Configuration.mdo under mdclass namespace)
# ---------------------------------------------------------------------------


def _parse_config_mdo(mdo_path: str, directory: str) -> ExtensionInfo | None:
    """Parse EDT-format Configuration.mdo and determine role."""
    try:
        tree = ET.parse(mdo_path)
    except (ET.ParseError, OSError):
        return None

    root = tree.getroot()

    # Root element is <mdclass:Configuration ...>
    # Direct children: <name>, <namePrefix>, <configurationExtensionPurpose>, <extension>
    name = _mdo_child_text(root, "name")
    name_prefix = _mdo_child_text(root, "namePrefix")
    ext_purpose = _mdo_child_text(root, "configurationExtensionPurpose")

    # Also check for <extension xsi:type="mdclassExtension:ConfigurationExtension">
    has_extension_el = False
    for child in root:
        local = _local_tag(child.tag)
        if local == "extension":
            has_extension_el = True
            break

    if ext_purpose or has_extension_el:
        role = ConfigRole.EXTENSION
    else:
        role = ConfigRole.MAIN

    return ExtensionInfo(
        path=directory,
        role=role,
        name=name,
        purpose=ext_purpose,
        name_prefix=name_prefix,
        source_format="edt",
    )


def _mdo_child_text(parent, local_name: str) -> str:
    """Get text of a direct child by local tag name (ignoring namespace)."""
    for child in parent:
        if _local_tag(child.tag) == local_name:
            return (child.text or "").strip()
    return ""


def _local_tag(tag: str) -> str:
    """Strip namespace from an XML tag: '{ns}local' -> 'local'."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


# ---------------------------------------------------------------------------
# Single-directory detection
# ---------------------------------------------------------------------------


def _detect_single(directory: str) -> ExtensionInfo | None:
    """Detect whether *directory* is a 1C configuration (main or extension).

    Checks:
    1. Configuration.xml in directory root (CF format)
    2. Any subdirectory containing Configuration.mdo (EDT format)
    3. One level of subdirectories for the same checks (wrapper dirs)

    Returns ExtensionInfo or None if not a 1C configuration.
    """
    try:
        base = Path(directory)
        if not base.is_dir():
            return None

        # 1. Check Configuration.xml directly
        cfg_xml = base / "Configuration.xml"
        if cfg_xml.is_file():
            result = _parse_config_xml(str(cfg_xml), directory)
            if result is not None:
                return result

        # 2. Check */Configuration.mdo (EDT: Configuration/Configuration.mdo)
        result = _scan_for_mdo(base, directory)
        if result is not None:
            return result

        # 3. One level deeper: check each subdirectory
        entries = list(base.iterdir())

        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue

            # CF: subdir/Configuration.xml
            sub_xml = entry / "Configuration.xml"
            if sub_xml.is_file():
                result = _parse_config_xml(str(sub_xml), str(entry))
                if result is not None:
                    return result

            # EDT: subdir/*/Configuration.mdo
            result = _scan_for_mdo(entry, str(entry))
            if result is not None:
                return result

        return None
    except OSError:
        return None


def _scan_for_mdo(base: Path, directory: str) -> ExtensionInfo | None:
    """Look for Configuration.mdo in immediate subdirectories of *base*."""
    try:
        entries = list(base.iterdir())
    except OSError:
        return None

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name in _SKIP_DIRS or entry.name.startswith("."):
            continue
        mdo = entry / "Configuration.mdo"
        if mdo.is_file():
            return _parse_config_mdo(str(mdo), directory)
    return None


# ---------------------------------------------------------------------------
# Context detection (main entry point)
# ---------------------------------------------------------------------------


def detect_extension_context(base_path: str) -> ExtensionContext:
    """Detect extension context for *base_path*.

    1. Determines if base_path is a main config or extension.
    2. Scans sibling directories (1-2 levels up) for related configs.
    3. Generates warnings for the AI agent.
    """
    current = _detect_single(base_path) or ExtensionInfo(
        path=base_path,
        role=ConfigRole.UNKNOWN,
    )

    siblings: list[ExtensionInfo] = []
    base = Path(base_path).resolve()

    # Scan siblings at parent level (-1), then grandparent (-2) if needed
    for level in range(1, 3):
        ancestor = base
        for _ in range(level):
            ancestor = ancestor.parent
        if ancestor == base or not ancestor.is_dir():
            continue

        found_any = False
        try:
            entries = sorted(ancestor.iterdir(), key=lambda p: p.name)
        except OSError:
            continue

        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            resolved_entry = entry.resolve()
            if resolved_entry == base:
                continue
            info = _detect_single(str(resolved_entry))
            if info is not None:
                # Avoid duplicates (same resolved path)
                if not any(Path(s.path).resolve() == Path(info.path).resolve() for s in siblings):
                    siblings.append(info)
                    found_any = True

        if found_any:
            break  # found at this level, no need to go higher

    # Partition siblings
    nearby_extensions = [s for s in siblings if s.role == ConfigRole.EXTENSION]
    nearby_mains = [s for s in siblings if s.role == ConfigRole.MAIN]
    nearby_main = nearby_mains[0] if nearby_mains else None

    # Build warnings
    warnings = _build_warnings(current, nearby_extensions, nearby_main)

    return ExtensionContext(
        current=current,
        nearby_extensions=nearby_extensions,
        nearby_main=nearby_main,
        warnings=warnings,
    )


def _build_warnings(
    current: ExtensionInfo,
    nearby_extensions: list[ExtensionInfo],
    nearby_main: ExtensionInfo | None,
) -> list[str]:
    """Build human-readable warnings about extension context."""
    warnings: list[str] = []

    if current.role == ConfigRole.MAIN and nearby_extensions:
        ext_list = ", ".join(
            f"{e.name or '?'} ({e.purpose or '?'}, prefix: {e.name_prefix or '—'}, path: {e.path})"
            for e in nearby_extensions
        )
        warnings.append(
            f"Extensions detected near main config: {ext_list}. "
            "Extension code can override methods via annotations "
            "&Перед/&После/&Вместо/&ИзменениеИКонтроль (Before/After/Instead/ChangeAndValidate)."
        )

    elif current.role == ConfigRole.EXTENSION:
        purpose_label = current.purpose or "unknown"
        name_label = current.name or "?"
        warnings.append(
            f"This is an EXTENSION '{name_label}' "
            f"(purpose: {purpose_label}, prefix: {current.name_prefix or '—'}). "
            "Analysis without the main config may be incomplete or misleading."
        )
        if nearby_main:
            warnings.append(f"Main config found nearby: {nearby_main.name or '?'} ({nearby_main.path})")

    return warnings


# ---------------------------------------------------------------------------
# Targeted override scanning
# ---------------------------------------------------------------------------


def find_extension_overrides(
    extension_path: str,
    object_name: str | None = None,
) -> list[dict]:
    """Find method interception annotations in BSL files of an extension.

    Args:
        extension_path: Root directory of the extension.
        object_name: If specified, only scan modules belonging to this object
            (case-insensitive match on object_name from path parsing).

    Returns:
        List of dicts with keys: annotation, target_method, extension_method,
        module_path, object_name, module_type, line.
    """
    ext_base = Path(extension_path)
    if not ext_base.is_dir():
        return []

    object_filter = object_name.lower() if object_name else None
    results: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(ext_base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".bsl"):
                continue

            fpath = os.path.join(dirpath, fname)
            rel_path = Path(fpath).relative_to(ext_base).as_posix()

            # Filter by object_name if specified
            if object_filter:
                bsl_info = parse_bsl_path(fpath, str(ext_base))
                if not bsl_info.object_name:
                    continue
                if bsl_info.object_name.lower() != object_filter:
                    continue

            _scan_bsl_for_annotations(fpath, rel_path, str(ext_base), results)

    return results


def _scan_bsl_for_annotations(
    fpath: str,
    rel_path: str,
    ext_base: str,
    results: list[dict],
) -> None:
    """Scan a single BSL file for interception annotations."""
    try:
        with open(fpath, encoding="utf-8-sig", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return

    bsl_info = parse_bsl_path(fpath, ext_base)

    for i, line in enumerate(lines):
        m = _ANNOTATION_RE.search(line)
        if m is None:
            continue

        annotation = m.group(1)
        target_method = m.group(2)

        # Try to find the procedure/function name on the next line(s)
        extension_method = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            pm = _PROC_DEF_RE.match(lines[j])
            if pm:
                extension_method = pm.group(1)
                break

        results.append(
            {
                "annotation": annotation,
                "target_method": target_method,
                "extension_method": extension_method,
                "module_path": rel_path,
                "object_name": bsl_info.object_name or "",
                "module_type": bsl_info.module_type or "",
                "line": i + 1,
            }
        )
