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
You are exploring a 1C BSL codebase.

CRITICAL: Large configs have 23,000+ files. grep/grep_read on broad paths
(path='.' or path='CommonModules') WILL timeout. ALWAYS:
  1. find_module('name_fragment') -> get file paths
  2. read_file(path) or grep(pattern, path=exact_file)

BSL HELPERS available in sandbox:
  find_module(name)           -- find BSL modules by name fragment
  find_by_type(type, name)    -- find by metadata category
  extract_procedures(path)    -- list all procedures in a file
  find_exports(path)          -- list exported procedures
  safe_grep(pattern, hint)    -- timeout-safe grep across files
  read_procedure(path, name)  -- extract single procedure body
  find_callers(proc, hint)    -- find who calls this procedure

FILE PATHS depend on format:
  CF:  CommonModules/Name/Ext/Module.bsl
  EDT: CommonModules/Name/Module.bsl\
"""


def get_strategy(effort: str, format_info) -> str:
    config = EFFORT_LEVELS.get(effort, EFFORT_LEVELS["medium"])

    parts = [_BASE_STRATEGY]

    parts.append(f"\nEFFORT LEVEL: {effort}")
    parts.append(config.guidance)
    parts.append(
        f"Limits: max_execute_calls={config.max_execute_calls}, "
        f"max_llm_calls={config.max_llm_calls}, "
        f"safe_grep_max_files={config.safe_grep_max_files}"
    )

    if format_info is not None:
        fmt = getattr(format_info, "format", None)
        if fmt == "CF":
            parts.append(
                "\nFORMAT: CF detected.\n"
                "Example paths:\n"
                "  CommonModules/MyModule/Ext/Module.bsl\n"
                "  Documents/MyDoc/Ext/DocObject.bsl"
            )
        elif fmt == "EDT":
            parts.append(
                "\nFORMAT: EDT detected.\n"
                "Example paths:\n"
                "  CommonModules/MyModule/Module.bsl\n"
                "  Documents/MyDoc/DocObject.bsl"
            )

    return "\n".join(parts)


RLM_START_DESCRIPTION = (
    "Start a BSL code exploration session on a 1C codebase.\n"
    "Returns session_id, detected config format, BSL helper functions, and exploration strategy.\n"
    "IMPORTANT: For large 1C configs (23K+ files), NEVER grep on broad paths -- use find_module() first."
)

RLM_EXECUTE_DESCRIPTION = (
    "Execute Python in the BSL sandbox.\n"
    "BSL helpers: find_module, find_by_type, extract_procedures, find_exports,\n"
    "safe_grep, read_procedure, find_callers.\n"
    "Standard: read_file, read_files, grep, grep_summary, grep_read, glob_files, tree.\n"
    "CRITICAL: grep on path='.' ALWAYS times out on large 1C configs. Use find_module() first."
)
