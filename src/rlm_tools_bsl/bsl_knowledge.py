from __future__ import annotations

from dataclasses import dataclass


BSL_PATTERNS = {
    "procedure_def": r"(Процедура|Функция|Procedure|Function)\s+(\w+)\s*\(([^)]*)\)\s*(Экспорт|Export)?",
    "procedure_end": r"^\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)",
    "export_marker": r"\)\s*(Экспорт|Export)\s*$",
    "module_call": r"(\w+)\.(\w+)\s*\(",
    "region_start": r"#(?:Область|Region)\s+(\w+)",
    "region_end": r"#(?:КонецОбласти|EndRegion)",
    "preprocessor_if": r"#(?:Если|If)\s+.+\s+(?:Тогда|Then)",
    "preprocessor_endif": r"#(?:КонецЕсли|EndIf)",
    "new_structure": r"(?:Новый|New)\s+(?:Структура|Structure)\(",
    "structure_insert": r'\.(?:Вставить|Insert)\(\s*"(\w+)"',
}


@dataclass
class EffortConfig:
    max_execute_calls: int
    max_llm_calls: int
    safe_grep_max_files: int
    guidance: str


EFFORT_LEVELS = {
    "low": EffortConfig(
        10, 5, 5, "Quick lookup. Find target module, extract what's needed, stop. Target: 3-5 rlm_execute calls."
    ),
    "medium": EffortConfig(
        25, 15, 10, "Standard analysis. Find modules, trace 1-2 levels of calls, summarize. Target: 10-15 calls."
    ),
    "high": EffortConfig(
        50,
        30,
        20,
        "Deep analysis (RECOMMENDED for multi-aspect tasks). Multi-module trace (3-4 levels), data flow, complete picture. Target: 20-30 calls. Build mermaid diagram.",
    ),
    "max": EffortConfig(
        100,
        50,
        50,
        "Exhaustive mapping. All modules, all call chains, all data flows. Use llm_query() for semantic analysis. Target: 40-50+ calls.",
    ),
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

Step 0 — UNDERSTAND: decode the business question
  Check _BUSINESS_RECIPES for guided analysis plan
  analyze_subsystem('ПодсистемаИмя') → all objects in the business domain

Step 1 — DISCOVER: find what you need
  search(query)                          → BROAD first pass: methods + objects + regions + headers + attributes + predefined
  find_module('name') or find_by_type('Documents', 'name') → get file paths
  search_objects('бизнес-имя')           → precise: find 1C OBJECTS by Russian synonym
  search_methods('substring')            → precise: find METHODS by code name (FTS)
  search_regions('имя')                  → precise: find code regions
  search_module_headers('текст')         → precise: find modules by header
  NOTE: search() = broad first pass; specialized helpers = precise follow-up when you need specific fields
  parse_object_xml(path) → attributes, tabular sections, dimensions, resources
  find_attributes('ИмяРеквизита')        → INSTANT: attribute name → type(s)
  find_predefined('ИмяПредопределённого') → INSTANT: predefined item → type(s)
  parse_form(object_name) → form handlers, commands, attributes (for UI/form analysis tasks)

Step 2 — READ: understand the code
  extract_procedures(path) → list all procedures with lines
  read_procedure(path, 'ProcName') → get procedure body (numbered)
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
  get_overrides('ObjectName') → indexed overrides (instant)
  read_procedure(path, name, include_overrides=True) → original + extension body
  extract_procedures includes overridden_by field
  NOTE: extension files are OUTSIDE the sandbox. Do NOT read them via read_file/glob_files.
  Use ONLY the helpers above — they read extension code internally.

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

_BUSINESS_RECIPES: dict[str, dict[str, list[str]]] = {
    "себестоимость": {
        "compact": [
            "search_objects('себестоимость') → объекты по синониму",
            "find_by_type('AccumulationRegisters', 'Себестоимость') → регистры",
            "find_register_writers('РегистрИмя') → документы-писатели",
            "analyze_document_flow('ДокИмя') → проводки + подписки",
        ],
        "full": [
            "search_objects('себестоимость') → документы, регистры, модули по синониму",
            "find_by_type('AccumulationRegisters', 'Себестоимость') → регистры себестоимости",
            "find_register_writers('РегистрИмя') → какие документы пишут в регистр",
            "analyze_document_flow('ДокИмя') → проводки + подписки + задания",
            "search_methods('Себестоимость') → методы расчёта по всей кодовой базе",
            "find_callers_context('РассчитатьСебестоимость') → цепочка вызовов",
            "analyze_subsystem('РасчетСебестоимости') → все объекты домена",
            "ALT: grep('Себестоимость', path=module) если регистр не найден",
        ],
    },
    "проведение": {
        "compact": [
            "search_objects('ДокИмя') → найти документ по бизнес-имени",
            "find_register_movements('ДокИмя') → какие регистры пишет",
            "analyze_document_flow('ДокИмя') → подписки + движения + задания",
        ],
        "full": [
            "search_objects('ДокИмя') → найти документ по бизнес-имени",
            "find_register_movements('ДокИмя') → регистры, в которые пишет документ",
            "analyze_document_flow('ДокИмя') → проводки + подписки + рег.задания",
            "find_event_subscriptions('ДокИмя') → подписки на события документа",
            "read_procedure(path, 'ОбработкаПроведения') → код проведения",
            "find_callers_context('ОбработкаПроведения') → кто вызывает проведение",
            "ALT: search_methods('Проведение') если имя процедуры нестандартное",
        ],
    },
    "распределение": {
        "compact": [
            "search_objects('распределение') → объекты по синониму",
            "search_methods('Распредел') → методы распределения",
            "find_register_writers('РегистрИмя') → документы-источники",
        ],
        "full": [
            "search_objects('распределение') → объекты по синониму",
            "search_methods('Распредел') → все методы распределения",
            "find_by_type('AccumulationRegisters', 'Распредел') → регистры распределения",
            "find_register_writers('РегистрИмя') → какие документы пишут в регистр",
            "analyze_document_flow('ДокИмя') → полный flow документа распределения",
            "analyze_subsystem('РаспределениеЗатрат') → все объекты домена",
            "find_callers_context('Распределить') → цепочка вызовов",
            "ALT: grep('Распредел', path=module) для поиска в конкретных модулях",
        ],
    },
    "печать": {
        "compact": [
            "search_objects('печат') → объекты печати по синониму",
            "find_print_forms('ОбъектИмя') → печатные формы объекта",
            "search_methods('Печат') → методы формирования печати",
        ],
        "full": [
            "search_objects('печат') → объекты печати по синониму",
            "find_print_forms('ОбъектИмя') → все печатные формы объекта",
            "find_module('Печать') → модули подсистемы печати",
            "search_methods('Печат') → методы формирования печатных форм",
            "find_callers_context('СформироватьПечатнуюФорму') → цепочка вызовов",
            "analyze_subsystem('Печать') → все объекты подсистемы печати",
            "find_by_type('CommonModules', 'Печат') → общие модули печати",
            "ALT: grep('ТабличныйДокумент', path=module) для поиска макетов",
        ],
    },
    "права": {
        "compact": [
            "search_objects('ОбъектИмя') → найти объект по бизнес-имени",
            "find_roles('ОбъектИмя') → роли с доступом к объекту",
            "find_functional_options('ОбъектИмя') → функциональные опции",
        ],
        "full": [
            "search_objects('ОбъектИмя') → найти объект по бизнес-имени",
            "find_roles('ОбъектИмя') → роли с правами на объект (чтение, запись, и т.д.)",
            "find_by_type('Roles') → полный список ролей конфигурации",
            "find_functional_options('ОбъектИмя') → функциональные опции объекта",
            "search_methods('ПравоДоступа') → проверки прав в коде",
            "search_methods('РольДоступна') → программные проверки ролей",
            "analyze_subsystem('УправлениеДоступом') → все объекты подсистемы прав",
            "ALT: grep('ПравоДоступа|РольДоступна', path=module) в конкретных модулях",
        ],
    },
    "интеграция": {
        "compact": [
            "search_objects('обмен') или search_objects('сервис') → объекты интеграции по синониму",
            "find_http_services() → HTTP endpoints (REST API)",
            "find_web_services() → SOAP операции",
        ],
        "full": [
            "search_objects('обмен') или search_objects('сервис') → объекты интеграции по синониму",
            "find_http_services() → HTTP endpoints (REST API)",
            "find_web_services() → SOAP операции",
            "find_xdto_packages() → XDTO контракты данных",
            "plans = find_by_type('ExchangePlans') → получить имена планов обмена",
            "find_exchange_plan_content('КонкретноеИмяПлана') → состав плана (передать реальное имя из шага 4)",
            "all_jobs = find_scheduled_jobs() → затем отфильтровать: [j for j in all_jobs if any(k in j['name'] for k in ('Обмен','Exchange','Синхрониз','Загруз','Выгруз'))]",
        ],
        "code_hint": (
            "# Готовый код для интеграционного анализа (можно вставить в rlm_execute):\n"
            "hs = find_http_services()\n"
            "ws = find_web_services()\n"
            "xdto = find_xdto_packages()\n"
            "plans = find_by_type('ExchangePlans')\n"
            "plan_names = sorted(set(p['object_name'] for p in plans))\n"
            "print(f'HTTP: {len(hs)}, SOAP: {len(ws)}, XDTO: {len(xdto)}, Plans: {len(plan_names)}')\n"
            "for name in plan_names[:3]:\n"
            "    ep = find_exchange_plan_content(name)\n"
            "    print(f'  {name}: {len(ep)} objects')\n"
            "all_jobs = find_scheduled_jobs()\n"
            "kw = ('Обмен','Exchange','Синхрониз','Загруз','Выгруз')\n"
            "ex_jobs = [j for j in all_jobs if any(k in j['name'] for k in kw)]\n"
            "print(f'Exchange jobs: {len(ex_jobs)} of {len(all_jobs)}')"
        ),
    },
    "события формы": {
        "compact": [
            "search_objects('ОбъектИмя') → найти объект по бизнес-имени",
            "forms = parse_form('ОбъектИмя') → список форм с handlers, commands, attributes, module_path",
            "for f in forms: if f['module_path']: extract_procedures(f['module_path'])",
        ],
        "full": [
            "search_objects('ОбъектИмя') → найти объект по бизнес-имени",
            "forms = parse_form('ОбъектИмя') → все формы с handlers/commands/attributes/module_path",
            "parse_form('ОбъектИмя', handler='ПроцИмя') → обратный поиск: к чему привязана процедура",
            "forms_with_code = [f for f in forms if f['module_path']]  # формы с BSL-модулем",
            "for f in forms_with_code: extract_procedures(f['module_path']) → процедуры каждой формы",
            "for f in forms_with_code: read_procedure(f['module_path'], 'ПриСозданииНаСервере') → код инициализации",
            "find_callers_context('ОбработчикИмя') → кто вызывает обработчик",
            "parse_object_xml(path) → метаданные объекта (реквизиты, ТЧ)",
        ],
    },
    "тип реквизита": {
        "compact": [
            "find_predefined('ИмяСубконто') — if asking about subconto/predefined",
            "find_attributes('ИмяРеквизита') — if asking about attribute type",
            "Done — types are in the result",
        ],
        "full": [
            "Step 1: find_predefined('Name') or find_attributes('Name')",
            "Step 2: If not found, parse_object_xml('Category/ObjectName') for on-demand parse",
            "Step 3: Report types from result",
        ],
        "code_hint": (
            "# Тип субконто / предопределённого:\n"
            "items = find_predefined('РеализуемыеАктивы')\n"
            "for i in items:\n"
            "    print(i['item_name'], i['types'])\n\n"
            "# Тип реквизита:\n"
            "attrs = find_attributes('Организация')\n"
            "for a in attrs:\n"
            "    print(a['object_name'], a['attr_name'], a['attr_type'])"
        ),
    },
}

_RECIPE_ALIASES: dict[str, str] = {
    "обмен": "интеграция",
    "синхрониз": "интеграция",
    "exchange": "интеграция",
    "обработчики формы": "события формы",
    "элементы формы": "события формы",
    "кнопки формы": "события формы",
    "субконто": "тип реквизита",
    "тип субконто": "тип реквизита",
    "предопределённ": "тип реквизита",
    "attribute type": "тип реквизита",
}

_STRATEGY_IO_SECTION = """\
File I/O:
  read_file(path), read_files(paths)       → str / dict (numbered in MCP session)
  grep(pattern, path), grep_summary(pattern), grep_read(pattern, path)
  glob_files(pattern), tree(path, max_depth=3), find_files(name)
  NOTE: For BSL modules prefer find_module()/find_by_type() over glob_files()
  NOTE: tree('.') on large configs produces too much output — use tree('SubDir') or find_files()
LLM (if available):
  llm_query(prompt, context='')            → str (keep context <3000 chars, split if empty response)
  llm_query_batched(prompts, context)      → [str]"""


def build_helpers_table(registry: dict) -> str:
    """Build the HELPERS section of strategy text from registry."""
    lines = ["== HELPERS (call help('keyword') for usage examples and return formats) =="]
    for cat_key, cat_label in _CATEGORY_ORDER:
        entries = [(name, entry["sig"]) for name, entry in registry.items() if entry["cat"] == cat_key]
        if not entries:
            continue
        lines.append(f"{cat_label}:")
        for _, sig in entries:
            lines.append(f"  {sig}")
    lines.append(_STRATEGY_IO_SECTION)
    return "\n".join(lines)


def _match_recipe(query: str) -> str | None:
    """Match query text against _BUSINESS_RECIPES domain keys and aliases."""
    q = query.lower()
    for domain in _BUSINESS_RECIPES:
        if domain in q:
            return domain
    for alias, domain in _RECIPE_ALIASES.items():
        if alias in q:
            return domain
    return None


def get_strategy(
    effort: str,
    format_info,
    detected_prefixes: list[str] | None = None,
    extension_context=None,
    ext_overrides: dict | None = None,
    registry: dict | None = None,
    idx_stats: dict | None = None,
    idx_warnings: list[str] | None = None,
    query: str = "",
) -> str:
    config = EFFORT_LEVELS.get(effort, EFFORT_LEVELS["medium"])

    has_extensions = (
        extension_context is not None
        and extension_context.current.role.value != "unknown"
        and (extension_context.current.role.value == "extension" or extension_context.nearby_extensions)
    )

    parts: list[str] = []

    # --- Extension alert (BEFORE everything else if present) ---
    if has_extensions:
        parts.append(_extension_strategy(extension_context, ext_overrides or {}))

    # --- Base strategy (critical, workflow) ---
    parts.append(_STRATEGY_HEADER)

    # --- Business recipe (dynamic injection based on query) ---
    if query:
        domain = _match_recipe(query)
        if domain:
            level = "compact" if effort in ("low", "medium") else "full"
            recipe = _BUSINESS_RECIPES[domain]
            steps = recipe[level]
            recipe_lines = [f"\n== BUSINESS RECIPE: {domain} =="]
            for i, step in enumerate(steps, 1):
                recipe_lines.append(f"  {i}. {step}")
            code_hint = recipe.get("code_hint")
            if code_hint:
                recipe_lines.append(f"\nReady-to-use code (paste into rlm_execute):\n```python\n{code_hint}\n```")
            parts.append("\n".join(recipe_lines))

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

        builder_version = idx_stats.get("builder_version") or "?"
        synonyms_count = idx_stats.get("object_synonyms", 0)

        idx_lines = ["\n== INDEX =="]
        label = f"Index v{builder_version} ({methods_count} methods, {calls_count} call edges"
        if synonyms_count:
            label += f", {synonyms_count} synonyms"
        oa_count = idx_stats.get("object_attributes", 0)
        pi_count = idx_stats.get("predefined_items", 0)
        if oa_count:
            label += f", {oa_count} attributes"
        if pi_count:
            label += f", {pi_count} predefined"
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
        instant_helpers.extend(
            [
                "find_event_subscriptions()",
                "find_scheduled_jobs()",
                "find_functional_options()",
            ]
        )
        role_rights_count = idx_stats.get("role_rights", 0)
        if role_rights_count:
            instant_helpers.append("find_roles()")
        register_movements_count = idx_stats.get("register_movements", 0)
        if register_movements_count:
            instant_helpers.extend(["find_register_movements()", "find_register_writers()"])
        file_paths_count = idx_stats.get("file_paths", 0)
        if file_paths_count:
            instant_helpers.extend(["glob_files(indexed)", "tree(indexed)", "find_files(indexed)"])
        if synonyms_count:
            instant_helpers.append("search_objects()")
        form_elements_count = idx_stats.get("form_elements", 0)
        if form_elements_count:
            instant_helpers.append("parse_form()")
        bver = int(idx_stats.get("builder_version") or 0)
        if bver >= 8:
            instant_helpers.append("search_regions()")
            instant_helpers.append("search_module_headers()")
        if oa_count:
            instant_helpers.append("find_attributes()")
        if pi_count:
            instant_helpers.append("find_predefined()")
        instant_helpers.append("search()")
        idx_lines.append(f"INSTANT from index: {', '.join(instant_helpers)}.")

        # FTS/synonym discovery
        if has_fts:
            idx_lines.append(
                "search_methods(query) — full-text search by method name substring. "
                "Use in Step 1 DISCOVER to find methods across the entire codebase without knowing the module name."
            )
        if synonyms_count:
            idx_lines.append(
                f"search_objects(query) — {synonyms_count} object synonyms indexed. "
                "Find 1C objects by Russian business name. Use in Step 1 DISCOVER."
            )

        # Workflow hints
        tips = [
            "INDEX TIPS:",
            "  - find_callers_context() returns instantly — no need to limit scope with hint, search the whole codebase.",
            "  - Batch 5-10 helpers per rlm_execute (index calls are <1ms each).",
            "  - extract_procedures + find_exports + find_callers_context in ONE call is fine.",
            "  - find_attributes() and find_predefined() are INSTANT from index — use for attribute/subconto type questions.",
        ]
        if file_paths_count:
            tips.extend(
                [
                    f"  - File navigation indexed: {file_paths_count} paths (.bsl/.mdo/.xml) — "
                    "glob_files(), tree(), find_files() are instant for supported patterns.",
                    "  - FAST: glob_files('**/*.mdo'), glob_files('Subsystems/**/*.mdo'), glob_files('Documents/**'), tree('Documents'), find_files('name')",
                    "  - SLOW (FS fallback): complex globs with multiple wildcards, glob_files('**/Dir*/*.xml')",
                    "  - For BSL modules: ALWAYS prefer find_module()/find_by_type() over glob_files() — faster and more precise.",
                    "  - NEVER use tree('.') on root of large configs — too much data. Use tree('SubDir') instead.",
                ]
            )
        idx_lines.append("\n".join(tips))

        idx_lines.append(
            "NOTE: Index freshness uses quick check (age + content sampling). "
            "Structural validation (files added/removed) is approximate — "
            "run 'rlm-bsl-index index info' for full check."
        )

        for w in idx_warnings or []:
            idx_lines.append(f"WARNING: {w}")
        parts.append("\n".join(idx_lines))
    else:
        parts.append(
            "\n== INDEX ==\n"
            "No pre-built index. All helpers work via filesystem fallback (slower on large configs).\n"
            "NEVER call rlm_index(action='build') — it takes 5-10 minutes and blocks the server. "
            "Only the USER decides when to build indexes. Work with what you have.\n"
            "WITHOUT INDEX:\n"
            "  - find_attributes(object_name='X') — WORKS (auto-resolves category via find_module, parses XML live)\n"
            "  - find_predefined(object_name='X') — WORKS (parses Predefined.xml live)\n"
            "  - find_attributes('name') without object_name — EMPTY (cannot scan all files)\n"
            "  - find_predefined('name') without object_name — EMPTY (cannot scan all files)\n"
            "  - search_methods, search_objects, search_regions — EMPTY (require index)\n"
            "  - parse_object_xml(path) — WORKS (always, direct XML read)\n"
            "  - All other helpers — WORK via filesystem (slower but functional)"
        )

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
                "\n== FORMAT: CF ==\nPaths: CommonModules/Name/Ext/Module.bsl, Documents/Name/Ext/ObjectModule.bsl"
            )
        elif fmt == "edt":
            parts.append("\n== FORMAT: EDT ==\nPaths: CommonModules/Name/Module.bsl, Documents/Name/ObjectModule.bsl")

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
            f"{e.name or '?'} (prefix: {e.name_prefix or '—'})" for e in ext_context.nearby_extensions
        )
        lines.append(
            f"\nCRITICAL — EXTENSIONS DETECTED: {ext_names}\n"
            "Extensions OVERRIDE methods in this config via annotations:\n"
            "  &Перед (Before), &После (After), &Вместо (Instead), &ИзменениеИКонтроль (ChangeAndValidate)\n"
            "YOU MUST mention overridden methods in your response.\n"
            "Extension files are OUTSIDE sandbox — do NOT use read_file/glob_files on extension paths.\n"
            "Use: get_overrides(), read_procedure(include_overrides=True), extract_procedures().overridden_by"
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
                f"  Main config found nearby: {ext_context.nearby_main.name or '?'} at {ext_context.nearby_main.path}"
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
        by_object[obj].append(f'&{ann}("{target}")')

    lines: list[str] = []
    for obj, obj_annotations in sorted(by_object.items()):
        lines.append(f"  {obj}: {', '.join(obj_annotations)}")
        if len(lines) >= max_lines:
            lines.append("  ... and more (see extension_context.own_overrides or nearby_extensions[].overrides)")
            break
    return lines


RLM_START_DESCRIPTION = (
    "Start a BSL code exploration session on a 1C codebase.\n"
    "Returns session_id, detected config format, BSL helper functions, and exploration strategy.\n"
    "IMPORTANT: Use effort='high' for any multi-aspect analysis (recommended default).\n"
    "Use effort='low' ONLY for single quick lookups (find one module, read one procedure).\n"
    "For large 1C configs (23K+ files), NEVER grep on broad paths -- use find_module() first.\n"
    "NEVER call rlm_index(action='build') yourself — index building takes 5-10 minutes and blocks I/O. "
    "If no index exists, work without it — all helpers have filesystem fallback."
)

RLM_EXECUTE_DESCRIPTION = (
    "Execute Python code in the BSL sandbox. The 'code' parameter is Python code.\n"
    "Call helper functions and use print() to see results. Variables persist between calls.\n"
    "Example: code=\"modules = find_module('MyModule')\\nfor m in modules:\\n    print(m['path'])\"\n"
    "BSL helpers: help, find_module, find_by_type, extract_procedures, find_exports,\n"
    "safe_grep, read_procedure, find_callers, find_callers_context, parse_object_xml,\n"
    "search, search_methods, search_objects, search_regions, search_module_headers,\n"
    "extract_queries, code_metrics, parse_form.\n"
    "Composite: analyze_object, analyze_subsystem, find_custom_modifications,\n"
    "find_event_subscriptions, find_scheduled_jobs, find_register_movements,\n"
    "find_register_writers, analyze_document_flow, find_based_on_documents,\n"
    "find_print_forms, find_functional_options, find_roles, find_enum_values,\n"
    "find_attributes, find_predefined.\n"
    "Standard: read_file, read_files, grep, grep_summary, grep_read, glob_files, tree, find_files.\n"
    "CRITICAL: grep on path='.' ALWAYS times out on large 1C configs. Use find_module() first."
)
