from __future__ import annotations

from dataclasses import dataclass


BSL_PATTERNS = {
    "procedure_def": r"(Процедура|Функция)\s+(\w+)\s*\(([^)]*)\)\s*(Экспорт)?",
    "procedure_end": r"^\s*(КонецПроцедуры|КонецФункции)",
    "export_marker": r"\)\s*Экспорт\s*$",
    "module_call": r"(\w+)\.(\w+)\s*\(",
    "region_start": r"#Область\s+(\w+)",
    "region_end": r"#КонецОбласти",
    "preprocessor_if": r"#Если\s+.+\s+Тогда",
    "preprocessor_endif": r"#КонецЕсли",
    "new_structure": r"Новый\s+Структура\(",
    "structure_insert": r'\.Вставить\(\s*"(\w+)"',
}


@dataclass
class EffortConfig:
    max_execute_calls: int
    max_llm_calls: int
    safe_grep_max_files: int
    guidance: str


EFFORT_LEVELS = {
    "low":    EffortConfig(10,  5,   5,  "Quick lookup. Find target module, extract what's needed, stop. Target: 3-5 rlm_execute calls."),
    "medium": EffortConfig(25,  15,  10, "Standard analysis. Find modules, trace 1-2 levels of calls, summarize. Target: 10-15 calls."),
    "high":   EffortConfig(50,  30,  20, "Deep analysis (RECOMMENDED for multi-aspect tasks). Multi-module trace (3-4 levels), data flow, complete picture. Target: 20-30 calls. Build mermaid diagram."),
    "max":    EffortConfig(100, 50,  50, "Exhaustive mapping. All modules, all call chains, all data flows. Use llm_query() for semantic analysis. Target: 40-50+ calls."),
}

_STRATEGY_HEADER = """\
You are exploring a 1C BSL codebase via Python sandbox.
Write Python code in rlm_execute. Use print() to output results.

== CRITICAL ==
Large configs have 23,000+ files. grep on broad paths WILL timeout. ALWAYS:
  1. find_module('name') → get file paths first
  2. Then read_file(path) or grep(pattern, path=specific_file)
If a helper returns an error, read the HINT at the end — it tells you what to do next.

== WORKFLOW ==
BEFORE YOU START: check rlm_start response — warnings, extension_context, detected_custom_prefixes.

Step 1 — DISCOVER: find what you need
  find_module('name') or find_by_type('Documents', 'name') → get file paths
  search_methods('substring') → find methods across all modules by name (needs index)
  parse_object_xml(path) → attributes, tabular sections, dimensions, resources

Step 2 — READ: understand the code
  extract_procedures(path) → list all procedures with lines
  read_procedure(path, 'ProcName') → get procedure body
  find_exports(path) → exported API of a module

Step 3 — TRACE: follow the call chains
  find_callers_context(proc, hint) → who calls this procedure
  safe_grep(pattern, hint) → search code patterns
  find_event_subscriptions(object_name) → what fires on write/post

Step 4 — ANALYZE: get the full picture
  analyze_object(name) → metadata + all modules + procedures
  analyze_document_flow(doc_name) → subscriptions + register movements + jobs
  find_custom_modifications(object_name) → find non-standard code by prefix
  find_register_movements(doc_name) → which registers a document writes to
  CAUTION: analyze_document_flow and analyze_object scan many files — on large configs (10K+)
  they may be slow (>60s). Prefer calling individual helpers separately if timeout occurs.

Step 5 — EXTENSIONS: check if behavior is modified
  If extensions detected (see warnings): find_ext_overrides(ext_path, 'ObjectName')
  In your response: ALWAYS mention overridden methods if any were found

== BATCHING & OUTPUT ==
Batch 3-5 related helpers per rlm_execute call — this is more efficient than one-at-a-time.
If output is truncated (ends with '... [truncated]'), split into smaller calls.
Print only summaries (counts, first N items) — never dump raw data.

Call help('keyword') for code recipes — e.g. help('exports'), help('movements'), help('flow')
"""

# Category display order and labels for strategy table
_CATEGORY_ORDER = [
    ("discovery", "Module discovery"),
    ("code", "Code analysis"),
    ("xml", "Metadata & XML"),
    ("composite", "Composite analysis"),
    ("business", "Business logic"),
    ("extension", "Extensions"),
    ("navigation", "Navigation"),
]

_STRATEGY_IO_SECTION = """\
File I/O:
  read_file(path), read_files(paths)       → str / dict
  grep(pattern, path), grep_summary(pattern), grep_read(pattern, path)
  glob_files(pattern), tree(path, max_depth=3), find_files(name)
LLM (if available):
  llm_query(prompt, context='')            → str (keep context <3000 chars, split if empty response)
  llm_query_batched(prompts, context)      → [str]"""


def build_helpers_table(registry: dict) -> str:
    """Build the HELPERS section of strategy text from registry."""
    lines = ["== HELPERS (call help('keyword') for usage examples and return formats) =="]
    for cat_key, cat_label in _CATEGORY_ORDER:
        entries = [
            (name, entry["sig"])
            for name, entry in registry.items()
            if entry["cat"] == cat_key
        ]
        if not entries:
            continue
        lines.append(f"{cat_label}:")
        for _, sig in entries:
            lines.append(f"  {sig}")
    lines.append(_STRATEGY_IO_SECTION)
    return "\n".join(lines)


def get_strategy(effort: str, format_info, detected_prefixes: list[str] | None = None,
                 extension_context=None, ext_overrides: dict | None = None,
                 registry: dict | None = None,
                 idx_stats: dict | None = None,
                 idx_warnings: list[str] | None = None) -> str:
    config = EFFORT_LEVELS.get(effort, EFFORT_LEVELS["medium"])

    has_extensions = (
        extension_context is not None
        and extension_context.current.role.value != "unknown"
        and (extension_context.current.role.value == "extension"
             or extension_context.nearby_extensions)
    )

    parts: list[str] = []

    # --- Extension alert (BEFORE everything else if present) ---
    if has_extensions:
        parts.append(_extension_strategy(extension_context, ext_overrides or {}))

    # --- Base strategy (critical, workflow) ---
    parts.append(_STRATEGY_HEADER)

    # --- Helpers table (dynamic from registry, or static fallback for IO/LLM) ---
    if registry:
        parts.append(build_helpers_table(registry))
    else:
        parts.append(_STRATEGY_IO_SECTION)

    # --- Index status ---
    if idx_stats is not None:
        methods_count = idx_stats.get("methods", 0)
        calls_count = idx_stats.get("calls", 0)
        config_name = idx_stats.get("config_name") or ""
        config_version = idx_stats.get("config_version") or ""
        has_fts = bool(idx_stats.get("has_fts"))

        idx_lines = ["\n== INDEX =="]
        label = f"Pre-built method index loaded ({methods_count} methods, {calls_count} call edges"
        if config_name:
            label += f", config: {config_name}"
            if config_version:
                label += f" v{config_version}"
        label += ")."
        idx_lines.append(label)

        # Speedup summary
        instant_helpers = ["extract_procedures()", "find_exports()"]
        if calls_count:
            instant_helpers.append("find_callers_context()")
        instant_helpers.extend([
            "find_event_subscriptions()", "find_scheduled_jobs()", "find_functional_options()",
        ])
        idx_lines.append(f"INSTANT from index: {', '.join(instant_helpers)}.")

        # FTS discovery
        if has_fts:
            idx_lines.append(
                "search_methods(query) — full-text search by method name substring. "
                "Use in Step 1 DISCOVER to find methods across the entire codebase without knowing the module name."
            )

        # Workflow hints
        idx_lines.append(
            "INDEX TIPS:\n"
            "  - find_callers_context() returns instantly — no need to limit scope with hint, search the whole codebase.\n"
            "  - Batch 5-10 helpers per rlm_execute (index calls are <1ms each).\n"
            "  - extract_procedures + find_exports + find_callers_context in ONE call is fine."
        )

        for w in (idx_warnings or []):
            idx_lines.append(f"WARNING: {w}")
        parts.append("\n".join(idx_lines))

    # --- Effort & limits ---
    parts.append(f"\n== EFFORT: {effort} ==")
    parts.append(config.guidance)
    parts.append(
        f"Limits: max_execute_calls={config.max_execute_calls}, "
        f"max_llm_calls={config.max_llm_calls}, "
        f"safe_grep_max_files={config.safe_grep_max_files}"
    )

    # --- Format & paths ---
    if format_info is not None:
        fmt = getattr(format_info, "format_label", None)
        if fmt == "cf":
            parts.append(
                "\n== FORMAT: CF ==\n"
                "Paths: CommonModules/Name/Ext/Module.bsl, Documents/Name/Ext/ObjectModule.bsl"
            )
        elif fmt == "edt":
            parts.append(
                "\n== FORMAT: EDT ==\n"
                "Paths: CommonModules/Name/Module.bsl, Documents/Name/ObjectModule.bsl"
            )

    # --- Custom prefixes ---
    if detected_prefixes:
        parts.append(
            f"\n== CUSTOM PREFIXES: {detected_prefixes} ==\n"
            "Use these to filter custom objects/subscriptions/roles. "
            "find_custom_modifications() uses them automatically."
        )

    return "\n".join(parts)


def _extension_strategy(ext_context, ext_overrides: dict) -> str:
    """Build strategy text for extension context."""
    from rlm_tools_bsl.extension_detector import ConfigRole

    current = ext_context.current
    lines: list[str] = []

    if current.role == ConfigRole.MAIN and ext_context.nearby_extensions:
        ext_names = ", ".join(
            f"{e.name or '?'} (prefix: {e.name_prefix or '—'})"
            for e in ext_context.nearby_extensions
        )
        lines.append(
            f"\nCRITICAL — EXTENSIONS DETECTED: {ext_names}\n"
            "Extensions OVERRIDE methods in this config via annotations:\n"
            "  &Перед (Before), &После (After), &Вместо (Instead), &ИзменениеИКонтроль (ChangeAndValidate)\n"
            "YOU MUST mention overridden methods in your response."
        )
        # Include auto-scanned overrides per extension
        for e in ext_context.nearby_extensions:
            overrides = ext_overrides.get(e.path, [])
            if overrides:
                lines.append(f"\nOverrides by {e.name or '?'} ({len(overrides)} total):")
                lines.extend(_format_overrides_summary(overrides))

    elif current.role == ConfigRole.EXTENSION:
        name_label = current.name or "?"
        purpose_label = current.purpose or "unknown"
        prefix_label = current.name_prefix or "—"
        lines.append(
            f"\nCRITICAL — THIS IS AN EXTENSION, NOT A MAIN CONFIG.\n"
            f"Extension: '{name_label}' (purpose: {purpose_label}, prefix: {prefix_label})\n"
            "Objects with ObjectBelonging=Adopted are borrowed from the main config.\n"
            "YOUR ANALYSIS IS INCOMPLETE without the main configuration.\n"
            "YOU MUST:\n"
            "  1. In your response, clearly state that this is an EXTENSION.\n"
            "  2. Warn the user that analysis without the main config may be misleading."
        )
        if ext_context.nearby_main:
            lines.append(
                f"  Main config found nearby: {ext_context.nearby_main.name or '?'} "
                f"at {ext_context.nearby_main.path}"
            )
        # Include auto-scanned own overrides
        overrides = ext_overrides.get("self", [])
        if overrides:
            lines.append(f"\nThis extension intercepts {len(overrides)} methods:")
            lines.extend(_format_overrides_summary(overrides))

    return "\n".join(lines)


def _format_overrides_summary(overrides: list[dict], max_lines: int = 30) -> list[str]:
    """Format overrides as compact grouped-by-object lines."""
    from collections import defaultdict
    by_object: dict[str, list[str]] = defaultdict(list)
    for o in overrides:
        obj = o.get("object_name") or "?"
        ann = o.get("annotation", "?")
        target = o.get("target_method", "?")
        by_object[obj].append(f"&{ann}(\"{target}\")")

    lines: list[str] = []
    for obj, annotations in sorted(by_object.items()):
        lines.append(f"  {obj}: {', '.join(annotations)}")
        if len(lines) >= max_lines:
            lines.append(f"  ... and more (see extension_context.own_overrides or nearby_extensions[].overrides)")
            break
    return lines


RLM_START_DESCRIPTION = (
    "Start a BSL code exploration session on a 1C codebase.\n"
    "Returns session_id, detected config format, BSL helper functions, and exploration strategy.\n"
    "IMPORTANT: Use effort='high' for any multi-aspect analysis (recommended default).\n"
    "Use effort='low' ONLY for single quick lookups (find one module, read one procedure).\n"
    "For large 1C configs (23K+ files), NEVER grep on broad paths -- use find_module() first."
)

RLM_EXECUTE_DESCRIPTION = (
    "Execute Python code in the BSL sandbox. The 'code' parameter is Python code.\n"
    "Call helper functions and use print() to see results. Variables persist between calls.\n"
    "Example: code=\"modules = find_module('MyModule')\\nfor m in modules:\\n    print(m['path'])\"\n"
    "BSL helpers: help, find_module, find_by_type, extract_procedures, find_exports,\n"
    "safe_grep, read_procedure, find_callers, find_callers_context, parse_object_xml.\n"
    "Composite: analyze_object, analyze_subsystem, find_custom_modifications,\n"
    "find_event_subscriptions, find_scheduled_jobs, find_register_movements,\n"
    "find_register_writers, analyze_document_flow, find_based_on_documents,\n"
    "find_print_forms, find_functional_options, find_roles, find_enum_values.\n"
    "Standard: read_file, read_files, grep, grep_summary, grep_read, glob_files, tree.\n"
    "CRITICAL: grep on path='.' ALWAYS times out on large 1C configs. Use find_module() first."
)
