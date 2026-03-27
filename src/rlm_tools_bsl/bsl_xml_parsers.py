from __future__ import annotations
import xml.etree.ElementTree as ET
from rlm_tools_bsl.format_detector import METADATA_CATEGORIES


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
    # XDTO packages
    "xdtopackage": "xdtopackages",
    "пакетxdto": "xdtopackages",
    # External data sources
    "externaldatasource": "externaldatasources",
    "внешнийисточникданных": "externaldatasources",
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
        attrs.append(
            {
                "name": _xml_find_text(props, "md:Name", ns),
                "synonym": _cf_find_synonym(props, ns),
                "type": _cf_parse_type(props, ns),
            }
        )
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
            dimensions.append(
                {
                    "name": _xml_find_text(dim_props, "md:Name", ns),
                    "synonym": _cf_find_synonym(dim_props, ns),
                    "type": _cf_parse_type(dim_props, ns),
                }
            )
    if dimensions:
        result["dimensions"] = dimensions

    resources = []
    for res_el in search_el.findall("md:Resource", ns):
        res_props = res_el.find("md:Properties", ns)
        if res_props is not None:
            resources.append(
                {
                    "name": _xml_find_text(res_props, "md:Name", ns),
                    "synonym": _cf_find_synonym(res_props, ns),
                    "type": _cf_parse_type(res_props, ns),
                }
            )
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
    attrs = []
    for ch in parent:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "attributes":
            continue
        attrs.append(
            {
                "name": _xml_direct_text(ch, "name"),
                "synonym": _mdo_find_synonym(ch),
                "type": _mdo_parse_type(ch),
            }
        )
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
        tab_sections.append(
            {
                "name": _xml_direct_text(ch, "name"),
                "synonym": _mdo_find_synonym(ch),
                "attributes": ts_attrs,
            }
        )
    if tab_sections:
        result["tabular_sections"] = tab_sections

    # Dimensions: <dimensions>
    dimensions = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "dimensions":
            continue
        dimensions.append(
            {
                "name": _xml_direct_text(ch, "name"),
                "synonym": _mdo_find_synonym(ch),
                "type": _mdo_parse_type(ch),
            }
        )
    if dimensions:
        result["dimensions"] = dimensions

    # Resources: <resources>
    resources = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "resources":
            continue
        resources.append(
            {
                "name": _xml_direct_text(ch, "name"),
                "synonym": _mdo_find_synonym(ch),
                "type": _mdo_parse_type(ch),
            }
        )
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


# --- EventSubscription XML parsers ---


def _parse_cf_event_subscription(xml_content: str) -> dict | None:
    """Parse CF-format EventSubscription XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    ns = _NS_CF
    sub_el = root.find("md:EventSubscription", ns)
    if sub_el is None:
        # Try without namespace
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "EventSubscription":
                sub_el = child
                break
    if sub_el is None:
        return None

    props = sub_el.find("md:Properties", ns)
    if props is None:
        for ch in sub_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break
    if props is None:
        return None

    name = _xml_find_text(props, "md:Name", ns)
    synonym = _cf_find_synonym(props, ns)
    event = _xml_find_text(props, "md:Event", ns)
    handler = _xml_find_text(props, "md:Handler", ns)

    # Source types: <Source><v8:Type>cfg:DocumentObject.Name</v8:Type>...</Source>
    source_types: list[str] = []
    source_el = props.find("md:Source", ns)
    if source_el is not None:
        for type_el in source_el.findall("v8:Type", ns):
            if type_el.text:
                raw = type_el.text.strip()
                # Strip cfg: prefix
                if raw.startswith("cfg:"):
                    raw = raw[4:]
                source_types.append(raw)

    return {
        "name": name,
        "synonym": synonym,
        "source_types": source_types,
        "event": event,
        "handler": handler,
    }


def _parse_mdo_event_subscription(xml_content: str) -> dict | None:
    """Parse EDT/MDO-format EventSubscription XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag != "EventSubscription":
        return None

    name = _xml_direct_text(root, "name")
    synonym = _mdo_find_synonym(root)
    event = _xml_direct_text(root, "event")
    handler = _xml_direct_text(root, "handler")

    # Source types: <source><types>DocumentObject.Name</types>...</source>
    source_types: list[str] = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local == "source":
            for t in ch:
                t_local = t.tag.split("}")[-1] if "}" in t.tag else t.tag
                if t_local == "types" and t.text:
                    source_types.append(t.text.strip())
            break

    return {
        "name": name,
        "synonym": synonym,
        "source_types": source_types,
        "event": event,
        "handler": handler,
    }


def parse_event_subscription_xml(xml_content: str) -> dict | None:
    """Parse EventSubscription XML, auto-detecting CF or EDT format."""
    if _MDO_NS_URI in xml_content:
        return _parse_mdo_event_subscription(xml_content)
    return _parse_cf_event_subscription(xml_content)


# --- ScheduledJob XML parsers ---


def _parse_cf_scheduled_job(xml_content: str) -> dict | None:
    """Parse CF-format ScheduledJob XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    ns = _NS_CF
    job_el = root.find("md:ScheduledJob", ns)
    if job_el is None:
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "ScheduledJob":
                job_el = child
                break
    if job_el is None:
        return None

    props = job_el.find("md:Properties", ns)
    if props is None:
        for ch in job_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break
    if props is None:
        return None

    name = _xml_find_text(props, "md:Name", ns)
    synonym = _cf_find_synonym(props, ns)
    method_name = _xml_find_text(props, "md:MethodName", ns)
    use_text = _xml_find_text(props, "md:Use", ns)
    predefined_text = _xml_find_text(props, "md:Predefined", ns)
    restart_count = _xml_find_text(props, "md:RestartCountOnFailure", ns)
    restart_interval = _xml_find_text(props, "md:RestartIntervalOnFailure", ns)

    return {
        "name": name,
        "synonym": synonym,
        "method_name": method_name,
        "use": use_text.lower() == "true" if use_text else True,
        "predefined": predefined_text.lower() == "true" if predefined_text else False,
        "restart_on_failure": {
            "count": int(restart_count) if restart_count else 0,
            "interval": int(restart_interval) if restart_interval else 0,
        },
    }


def _parse_mdo_scheduled_job(xml_content: str) -> dict | None:
    """Parse EDT/MDO-format ScheduledJob XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag != "ScheduledJob":
        return None

    name = _xml_direct_text(root, "name")
    synonym = _mdo_find_synonym(root)
    method_name = _xml_direct_text(root, "methodName")
    predefined_text = _xml_direct_text(root, "predefined")
    restart_count = _xml_direct_text(root, "restartCountOnFailure")
    restart_interval = _xml_direct_text(root, "restartIntervalOnFailure")

    return {
        "name": name,
        "synonym": synonym,
        "method_name": method_name,
        "use": True,  # EDT format doesn't have explicit <use> — defaults to true
        "predefined": predefined_text.lower() == "true" if predefined_text else False,
        "restart_on_failure": {
            "count": int(restart_count) if restart_count else 0,
            "interval": int(restart_interval) if restart_interval else 0,
        },
    }


def parse_scheduled_job_xml(xml_content: str) -> dict | None:
    """Parse ScheduledJob XML, auto-detecting CF or EDT format."""
    if _MDO_NS_URI in xml_content:
        return _parse_mdo_scheduled_job(xml_content)
    return _parse_cf_scheduled_job(xml_content)


# --- Enum XML parsers ---


def _parse_cf_enum(xml_content: str) -> dict | None:
    """Parse CF-format Enum XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    ns = _NS_CF
    enum_el = root.find("md:Enum", ns)
    if enum_el is None:
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "Enum":
                enum_el = child
                break
    if enum_el is None:
        return None

    props = enum_el.find("md:Properties", ns)
    if props is None:
        for ch in enum_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break
    if props is None:
        return None

    name = _xml_find_text(props, "md:Name", ns)
    synonym = _cf_find_synonym(props, ns)

    # Enum values live in ChildObjects
    child_objects = enum_el.find("md:ChildObjects", ns)
    search_el = child_objects if child_objects is not None else enum_el

    values: list[dict] = []
    for ev_el in search_el.findall("md:EnumValue", ns):
        ev_props = ev_el.find("md:Properties", ns)
        if ev_props is None:
            for ch in ev_el:
                if ch.tag.endswith("Properties"):
                    ev_props = ch
                    break
        if ev_props is None:
            continue
        values.append(
            {
                "name": _xml_find_text(ev_props, "md:Name", ns),
                "synonym": _cf_find_synonym(ev_props, ns),
            }
        )

    return {"name": name, "synonym": synonym, "values": values}


def _parse_mdo_enum(xml_content: str) -> dict | None:
    """Parse EDT/MDO-format Enum XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag != "Enum":
        return None

    name = _xml_direct_text(root, "name")
    synonym = _mdo_find_synonym(root)

    values: list[dict] = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local == "enumValues":
            val_name = _xml_direct_text(ch, "name")
            val_synonym = _mdo_find_synonym(ch)
            values.append({"name": val_name, "synonym": val_synonym})

    return {"name": name, "synonym": synonym, "values": values}


def parse_enum_xml(xml_content: str) -> dict | None:
    """Parse Enum XML, auto-detecting CF or EDT format."""
    if _MDO_NS_URI in xml_content:
        return _parse_mdo_enum(xml_content)
    return _parse_cf_enum(xml_content)


# --- FunctionalOption XML parsers ---


def _parse_cf_functional_option(xml_content: str) -> dict | None:
    """Parse CF-format FunctionalOption XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    ns = _NS_CF
    fo_el = root.find("md:FunctionalOption", ns)
    if fo_el is None:
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "FunctionalOption":
                fo_el = child
                break
    if fo_el is None:
        return None

    props = fo_el.find("md:Properties", ns)
    if props is None:
        for ch in fo_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break
    if props is None:
        return None

    name = _xml_find_text(props, "md:Name", ns)
    synonym = _cf_find_synonym(props, ns)
    location = _xml_find_text(props, "md:Location", ns)

    # Content: <Content><xr:Object>...</xr:Object>...</Content>
    content: list[str] = []
    content_el = props.find("md:Content", ns)
    if content_el is not None:
        for obj_el in content_el.findall("xr:Object", ns):
            if obj_el.text:
                content.append(obj_el.text.strip())

    return {"name": name, "synonym": synonym, "location": location, "content": content}


def _parse_mdo_functional_option(xml_content: str) -> dict | None:
    """Parse EDT/MDO-format FunctionalOption XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag != "FunctionalOption":
        return None

    name = _xml_direct_text(root, "name")
    synonym = _mdo_find_synonym(root)
    location = _xml_direct_text(root, "location")

    content: list[str] = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local == "content" and ch.text:
            content.append(ch.text.strip())

    return {"name": name, "synonym": synonym, "location": location, "content": content}


def parse_functional_option_xml(xml_content: str) -> dict | None:
    """Parse FunctionalOption XML, auto-detecting CF or EDT format."""
    if _MDO_NS_URI in xml_content:
        return _parse_mdo_functional_option(xml_content)
    return _parse_cf_functional_option(xml_content)


# --- Rights XML parser ---

# --- HTTPService XML parsers ---


def _parse_cf_http_service(xml_content: str) -> dict | None:
    """Parse CF-format HTTPService XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    ns = _NS_CF
    svc_el = root.find("md:HTTPService", ns)
    if svc_el is None:
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "HTTPService":
                svc_el = child
                break
    if svc_el is None:
        return None

    props = svc_el.find("md:Properties", ns)
    if props is None:
        for ch in svc_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break
    if props is None:
        return None

    name = _xml_find_text(props, "md:Name", ns)
    root_url = _xml_find_text(props, "md:RootURL", ns)

    templates: list[dict] = []
    child_objects = svc_el.find("md:ChildObjects", ns)
    if child_objects is None:
        for ch in svc_el:
            if ch.tag.endswith("ChildObjects"):
                child_objects = ch
                break
    if child_objects is not None:
        for tmpl_el in child_objects:
            tag = tmpl_el.tag.split("}")[-1] if "}" in tmpl_el.tag else tmpl_el.tag
            if tag != "URLTemplate":
                continue
            tmpl_props = tmpl_el.find("md:Properties", ns)
            if tmpl_props is None:
                for ch in tmpl_el:
                    if ch.tag.endswith("Properties"):
                        tmpl_props = ch
                        break
            if tmpl_props is None:
                continue
            tmpl_name = _xml_find_text(tmpl_props, "md:Name", ns)
            tmpl_template = _xml_find_text(tmpl_props, "md:Template", ns)

            methods: list[dict] = []
            tmpl_children = tmpl_el.find("md:ChildObjects", ns)
            if tmpl_children is None:
                for ch in tmpl_el:
                    if ch.tag.endswith("ChildObjects"):
                        tmpl_children = ch
                        break
            if tmpl_children is not None:
                for method_el in tmpl_children:
                    m_tag = method_el.tag.split("}")[-1] if "}" in method_el.tag else method_el.tag
                    if m_tag != "Method":
                        continue
                    m_props = method_el.find("md:Properties", ns)
                    if m_props is None:
                        for ch in method_el:
                            if ch.tag.endswith("Properties"):
                                m_props = ch
                                break
                    if m_props is None:
                        continue
                    methods.append(
                        {
                            "name": _xml_find_text(m_props, "md:Name", ns),
                            "http_method": _xml_find_text(m_props, "md:HTTPMethod", ns),
                            "handler": _xml_find_text(m_props, "md:Handler", ns),
                        }
                    )

            templates.append(
                {
                    "name": tmpl_name,
                    "template": tmpl_template,
                    "methods": methods,
                }
            )

    return {"name": name, "root_url": root_url, "templates": templates}


def _parse_mdo_http_service(xml_content: str) -> dict | None:
    """Parse EDT/MDO-format HTTPService XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag != "HTTPService":
        return None

    name = _xml_direct_text(root, "name")
    root_url = _xml_direct_text(root, "rootURL")

    templates: list[dict] = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "urlTemplates":
            continue
        tmpl_name = _xml_direct_text(ch, "name")
        tmpl_template = _xml_direct_text(ch, "template")
        methods: list[dict] = []
        for m in ch:
            m_local = m.tag.split("}")[-1] if "}" in m.tag else m.tag
            if m_local != "methods":
                continue
            methods.append(
                {
                    "name": _xml_direct_text(m, "name"),
                    "http_method": _xml_direct_text(m, "httpMethod"),
                    "handler": _xml_direct_text(m, "handler"),
                }
            )
        templates.append(
            {
                "name": tmpl_name,
                "template": tmpl_template,
                "methods": methods,
            }
        )

    return {"name": name, "root_url": root_url, "templates": templates}


def parse_http_service_xml(xml_content: str) -> dict | None:
    """Parse HTTPService XML, auto-detecting CF or EDT format."""
    if _MDO_NS_URI in xml_content:
        return _parse_mdo_http_service(xml_content)
    return _parse_cf_http_service(xml_content)


# --- WebService XML parsers ---


def _parse_cf_web_service(xml_content: str) -> dict | None:
    """Parse CF-format WebService XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    ns = _NS_CF
    svc_el = root.find("md:WebService", ns)
    if svc_el is None:
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "WebService":
                svc_el = child
                break
    if svc_el is None:
        return None

    props = svc_el.find("md:Properties", ns)
    if props is None:
        for ch in svc_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break
    if props is None:
        return None

    name = _xml_find_text(props, "md:Name", ns)
    namespace = _xml_find_text(props, "md:Namespace", ns)

    operations: list[dict] = []
    child_objects = svc_el.find("md:ChildObjects", ns)
    if child_objects is None:
        for ch in svc_el:
            if ch.tag.endswith("ChildObjects"):
                child_objects = ch
                break
    if child_objects is not None:
        for op_el in child_objects:
            tag = op_el.tag.split("}")[-1] if "}" in op_el.tag else op_el.tag
            if tag != "Operation":
                continue
            op_props = op_el.find("md:Properties", ns)
            if op_props is None:
                for ch in op_el:
                    if ch.tag.endswith("Properties"):
                        op_props = ch
                        break
            if op_props is None:
                continue

            op_name = _xml_find_text(op_props, "md:Name", ns)
            return_type = _xml_find_text(op_props, "md:XDTOReturningValueType", ns)
            procedure_name = _xml_find_text(op_props, "md:ProcedureName", ns)

            params: list[str] = []
            op_children = op_el.find("md:ChildObjects", ns)
            if op_children is None:
                for ch in op_el:
                    if ch.tag.endswith("ChildObjects"):
                        op_children = ch
                        break
            if op_children is not None:
                for param_el in op_children:
                    p_tag = param_el.tag.split("}")[-1] if "}" in param_el.tag else param_el.tag
                    if p_tag != "Parameter":
                        continue
                    p_props = param_el.find("md:Properties", ns)
                    if p_props is None:
                        for ch in param_el:
                            if ch.tag.endswith("Properties"):
                                p_props = ch
                                break
                    if p_props is not None:
                        p_name = _xml_find_text(p_props, "md:Name", ns)
                        if p_name:
                            params.append(p_name)

            operations.append(
                {
                    "name": op_name,
                    "return_type": return_type,
                    "procedure_name": procedure_name,
                    "params": params,
                }
            )

    return {"name": name, "namespace": namespace, "operations": operations}


def _parse_mdo_web_service(xml_content: str) -> dict | None:
    """Parse EDT/MDO-format WebService XML."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag != "WebService":
        return None

    name = _xml_direct_text(root, "name")
    namespace = _xml_direct_text(root, "namespace")

    operations: list[dict] = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "operations":
            continue
        op_name = _xml_direct_text(ch, "name")
        procedure_name = _xml_direct_text(ch, "procedureName")

        # Return type: compound <xdtoReturningValueType><name>string</name><nsUri>...</nsUri>
        return_type = ""
        for sub in ch:
            sub_local = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
            if sub_local == "xdtoReturningValueType":
                type_name = _xml_direct_text(sub, "name")
                ns_uri = _xml_direct_text(sub, "nsUri")
                if ns_uri == "http://www.w3.org/2001/XMLSchema" and type_name:
                    return_type = f"xs:{type_name}"
                elif type_name:
                    return_type = type_name
                break

        params: list[str] = []
        for sub in ch:
            sub_local = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
            if sub_local == "parameters":
                p_name = _xml_direct_text(sub, "name")
                if p_name:
                    params.append(p_name)

        operations.append(
            {
                "name": op_name,
                "return_type": return_type,
                "procedure_name": procedure_name,
                "params": params,
            }
        )

    return {"name": name, "namespace": namespace, "operations": operations}


def parse_web_service_xml(xml_content: str) -> dict | None:
    """Parse WebService XML, auto-detecting CF or EDT format."""
    if _MDO_NS_URI in xml_content:
        return _parse_mdo_web_service(xml_content)
    return _parse_cf_web_service(xml_content)


# --- XDTOPackage XML parsers ---

_NS_XDTO = "http://v8.1c.ru/8.1/xdto"


def _parse_cf_xdto_package(xml_content: str) -> dict | None:
    """Parse CF-format XDTOPackage XML (metadata only, types are binary)."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    ns = _NS_CF
    pkg_el = root.find("md:XDTOPackage", ns)
    if pkg_el is None:
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "XDTOPackage":
                pkg_el = child
                break
    if pkg_el is None:
        return None

    props = pkg_el.find("md:Properties", ns)
    if props is None:
        for ch in pkg_el:
            if ch.tag.endswith("Properties"):
                props = ch
                break
    if props is None:
        return None

    name = _xml_find_text(props, "md:Name", ns)
    namespace = _xml_find_text(props, "md:Namespace", ns)

    return {"name": name, "namespace": namespace, "types": []}


def _parse_mdo_xdto_package(xml_content: str) -> dict | None:
    """Parse EDT/MDO-format XDTOPackage .mdo (metadata only)."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag != "XDTOPackage":
        return None

    name = _xml_direct_text(root, "name")
    namespace = _xml_direct_text(root, "namespace")

    return {"name": name, "namespace": namespace, "types": []}


def parse_xdto_types(xdto_content: str) -> list[dict]:
    """Parse .xdto file (Package.xdto) — extract objectType/valueType with properties.
    Works only for EDT format (CF stores types as binary Package.bin)."""
    try:
        root = ET.fromstring(xdto_content)
    except ET.ParseError:
        return []

    types: list[dict] = []

    for child in root:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local not in ("objectType", "valueType"):
            continue
        type_name = child.get("name", "")
        properties: list[dict] = []
        for prop in child:
            p_local = prop.tag.split("}")[-1] if "}" in prop.tag else prop.tag
            if p_local == "property":
                properties.append(
                    {
                        "name": prop.get("name", ""),
                        "type": prop.get("type", ""),
                    }
                )
        types.append(
            {
                "name": type_name,
                "kind": local,
                "properties": properties,
            }
        )

    return types


def parse_xdto_package_xml(xml_content: str, xdto_content: str = "") -> dict | None:
    """Parse XDTOPackage, auto-detecting CF or EDT.
    If xdto_content provided, parse types from it."""
    if _MDO_NS_URI in xml_content:
        result = _parse_mdo_xdto_package(xml_content)
    else:
        result = _parse_cf_xdto_package(xml_content)
    if result and xdto_content:
        result["types"] = parse_xdto_types(xdto_content)
    return result


# --- ExchangePlan content parsers ---

_NS_EP_CF = "http://v8.1c.ru/8.3/xcf/extrnprops"


def _parse_cf_exchange_plan_content(xml_content: str) -> list[dict]:
    """Parse CF ExchangePlans/<Name>/Ext/Content.xml."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []

    ns = {"ep": _NS_EP_CF}
    items: list[dict] = []

    # Try with namespace
    for item_el in root.findall("ep:Item", ns):
        metadata = ""
        auto_record = False
        meta_el = item_el.find("ep:Metadata", ns)
        if meta_el is not None and meta_el.text:
            metadata = meta_el.text.strip()
        ar_el = item_el.find("ep:AutoRecord", ns)
        if ar_el is not None and ar_el.text:
            auto_record = ar_el.text.strip() == "Allow"
        if metadata:
            items.append({"ref": metadata, "auto_record": auto_record})

    # Fallback: no namespace
    if not items:
        for item_el in root.findall("Item"):
            metadata = ""
            auto_record = False
            meta_el = item_el.find("Metadata")
            if meta_el is not None and meta_el.text:
                metadata = meta_el.text.strip()
            ar_el = item_el.find("AutoRecord")
            if ar_el is not None and ar_el.text:
                auto_record = ar_el.text.strip() == "Allow"
            if metadata:
                items.append({"ref": metadata, "auto_record": auto_record})

    return items


def _parse_mdo_exchange_plan_content(mdo_content: str) -> list[dict]:
    """Extract <content> elements from EDT ExchangePlan .mdo."""
    try:
        root = ET.fromstring(mdo_content)
    except ET.ParseError:
        return []

    items: list[dict] = []
    for ch in root:
        local = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if local != "content":
            continue
        md_object = _xml_direct_text(ch, "mdObject")
        auto_record_text = _xml_direct_text(ch, "autoRecord")
        auto_record = auto_record_text == "Allow"
        if md_object:
            items.append({"ref": md_object, "auto_record": auto_record})

    return items


def parse_exchange_plan_content(xml_content: str) -> list[dict]:
    """Parse exchange plan content, auto-detecting format."""
    if _MDO_NS_URI in xml_content:
        return _parse_mdo_exchange_plan_content(xml_content)
    if _NS_EP_CF in xml_content or "ExchangePlanContent" in xml_content:
        return _parse_cf_exchange_plan_content(xml_content)
    return []


_NS_RIGHTS_VERSIONS = [
    "http://v8.1c.ru/8.2/roles",
    "http://v8.1c.ru/8.3/roles",
]


def parse_rights_xml(xml_content: str, object_filter: str = "") -> list[dict]:
    """Parse Rights XML (same format for CF and EDT).
    Returns only rights with <value>true</value>.
    Filters by object_filter substring if provided.
    Supports both 8.2 and 8.3 namespace versions."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []

    # Detect namespace from root tag
    root_ns = ""
    if "}" in root.tag:
        root_ns = root.tag.split("}")[0].lstrip("{")

    # Try detected namespace, then known versions, then no namespace
    ns_candidates = []
    if root_ns:
        ns_candidates.append({"r": root_ns})
    for ns_uri in _NS_RIGHTS_VERSIONS:
        ns_candidates.append({"r": ns_uri})

    results: list[dict] = []

    for ns in ns_candidates:
        obj_elements = root.findall("r:object", ns)
        if not obj_elements:
            continue

        for obj_el in obj_elements:
            obj_name_el = obj_el.find("r:name", ns)
            if obj_name_el is None or not obj_name_el.text:
                continue
            obj_name = obj_name_el.text.strip()

            if object_filter and object_filter not in obj_name:
                continue

            granted: list[str] = []
            for right_el in obj_el.findall("r:right", ns):
                right_name_el = right_el.find("r:name", ns)
                right_value_el = right_el.find("r:value", ns)
                if right_name_el is None or right_value_el is None:
                    continue
                if right_value_el.text and right_value_el.text.strip().lower() == "true":
                    granted.append(right_name_el.text.strip())

            if granted:
                results.append({"object": obj_name, "rights": granted})

        break  # Found working namespace, stop trying

    return results
