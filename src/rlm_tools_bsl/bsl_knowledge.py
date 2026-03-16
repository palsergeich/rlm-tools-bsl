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
    "high":   EffortConfig(50,  30,  20, "Deep analysis. Multi-module trace (3-4 levels), data flow, complete picture. Target: 20-30 calls. Build mermaid diagram."),
    "max":    EffortConfig(100, 50,  50, "Exhaustive mapping. All modules, all call chains, all data flows. Use llm_query() for semantic analysis. Target: 40-50+ calls."),
}

_BASE_STRATEGY = """\
You are exploring a 1C BSL codebase via Python sandbox.
Write Python code in rlm_execute. Use print() to output results.

== CRITICAL ==
Large configs have 23,000+ files. grep on broad paths WILL timeout. ALWAYS:
  1. find_module('name') → get file paths first
  2. Then read_file(path) or grep(pattern, path=specific_file)

== WORKFLOW ==
BEFORE YOU START: check rlm_start response — warnings, extension_context, detected_custom_prefixes.

Step 1 — DISCOVER: find what you need
  find_module('name') or find_by_type('Documents', 'name') → get file paths
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

Step 5 — EXTENSIONS: check if behavior is modified
  If extensions detected (see warnings): find_ext_overrides(ext_path, 'ObjectName')
  In your response: ALWAYS mention overridden methods if any were found

Call help('keyword') for code recipes — e.g. help('exports'), help('movements'), help('flow')

== HELPERS (call help('keyword') for usage examples and return formats) ==
Module discovery:
  find_module(name)                        → [{path, category, object_name, module_type}]
  find_by_type(category, name='')          → same. Categories: Documents, Catalogs, CommonModules, InformationRegisters, AccumulationRegisters, Reports, DataProcessors
Code analysis:
  extract_procedures(path)                 → [{name, type, line, end_line, is_export, params}]
  find_exports(path)                       → [{name, line, is_export, type, params}]
  read_procedure(path, proc_name)          → str | None
  find_callers_context(proc, hint, 0, 50)  → {callers: [{file, caller_name, line, ...}], _meta: {total_files, has_more}}
  find_callers(proc, hint, max_files=20)   → [{file, line, text}]
  safe_grep(pattern, hint, max_files=20)   → [{file, line, text}]
Metadata & XML:
  parse_object_xml(path)                   → {name, synonym, attributes, tabular_sections, dimensions, resources, ...}
  find_enum_values(enum_name)              → {name, synonym, values: [{name, synonym}]}
Composite analysis:
  analyze_object(name)                     → full profile: metadata + modules + procedures + exports
  analyze_document_flow(doc_name)          → metadata + subscriptions + register movements + jobs
  analyze_subsystem(name)                  → composition, custom vs standard objects
  find_custom_modifications(obj, pfx=None) → custom procedures, regions, attributes
Business logic:
  find_event_subscriptions(obj, custom_only=False) → [{event, handler, handler_module, handler_procedure, ...}]
  find_scheduled_jobs(name='')             → [{name, method_name, use, ...}]
  find_register_movements(doc_name)        → {code_registers, erp_mechanisms, manager_tables, adapted_registers}
  find_register_writers(reg_name)          → {writers: [{document, file, lines}]}
  find_based_on_documents(doc_name)        → {can_create_from_here, can_be_created_from}
  find_print_forms(obj_name)              → {print_forms: [{name, presentation}]}
  find_functional_options(obj_name)       → {xml_options, code_options}
  find_roles(obj_name)                    → {roles: [{role_name, rights}]}
Extensions:
  detect_extensions()                      → {config_role, nearby_extensions, nearby_main, warnings}
  find_ext_overrides(ext_path, obj='')     → {overrides: [{annotation, target_method, extension_method, ...}]}
File I/O:
  read_file(path), read_files(paths)       → str / dict
  grep(pattern, path), grep_summary(pattern), grep_read(pattern, path)
  glob_files(pattern), tree(path, max_depth=3), find_files(name)
LLM (if available):
  llm_query(prompt, context='')            → str (keep context <3000 chars, split if empty response)
  llm_query_batched(prompts, context)      → [str]\
"""


def get_strategy(effort: str, format_info, detected_prefixes: list[str] | None = None, extension_context=None) -> str:
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
        parts.append(_extension_strategy(extension_context))

    # --- Base strategy (critical, workflow, helpers table) ---
    parts.append(_BASE_STRATEGY)

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


def _extension_strategy(ext_context) -> str:
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
            "Extensions can OVERRIDE methods in this config via annotations:\n"
            "  &Перед (Before), &После (After), &Вместо (Instead), &ИзменениеИКонтроль (ChangeAndValidate)\n"
            "YOUR ANALYSIS MAY BE INCOMPLETE without checking extensions.\n"
            "YOU MUST:\n"
            "  1. When analyzing any object/module, call find_ext_overrides(ext_path, 'ObjectName')\n"
            "     to check if extensions override its methods.\n"
            "  2. In your final response, ALWAYS mention which methods are overridden by extensions.\n"
            "  3. If the user did not ask about extensions, still note: 'Extensions exist that may\n"
            "     modify this behavior' with the list of overridden methods.\n"
            "Extension paths (use with find_ext_overrides):"
        )
        for e in ext_context.nearby_extensions:
            lines.append(
                f"  - '{e.path}' ({e.name}, {e.purpose or '?'})"
            )

    elif current.role == ConfigRole.EXTENSION:
        name_label = current.name or "?"
        purpose_label = current.purpose or "unknown"
        prefix_label = current.name_prefix or "—"
        lines.append(
            f"\nCRITICAL — THIS IS AN EXTENSION, NOT A MAIN CONFIG.\n"
            f"Extension: '{name_label}' (purpose: {purpose_label}, prefix: {prefix_label})\n"
            "Objects with ObjectBelonging=Adopted are borrowed from the main config.\n"
            "Annotations &Перед/&После/&Вместо/&ИзменениеИКонтроль intercept main config methods.\n"
            "YOUR ANALYSIS IS INCOMPLETE without the main configuration.\n"
            "YOU MUST:\n"
            "  1. In your final response, clearly state that this is an EXTENSION, not the main config.\n"
            "  2. Warn the user that analysis of extension code alone may be misleading.\n"
            "  3. Call find_ext_overrides('', '') to list all intercepted methods in this extension."
        )
        if ext_context.nearby_main:
            lines.append(
                f"  Main config found nearby: {ext_context.nearby_main.name or '?'} "
                f"at {ext_context.nearby_main.path}"
            )

    return "\n".join(lines)


RLM_START_DESCRIPTION = (
    "Start a BSL code exploration session on a 1C codebase.\n"
    "Returns session_id, detected config format, BSL helper functions, and exploration strategy.\n"
    "IMPORTANT: For large 1C configs (23K+ files), NEVER grep on broad paths -- use find_module() first."
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
