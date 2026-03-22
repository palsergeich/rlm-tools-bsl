# Changelog

## [Unreleased]

## [1.3.4] — 2026-03-22

### Добавлено
- **Indexed glob `Dir/**/*.ext`** — новая стратегия `prefix_recursive_ext` в `_can_index_glob()`. Паттерн `Subsystems/**/*.mdo` теперь мгновенный из SQLite вместо FS fallback (2.8s на медленном ПК)
- **Warmup тяжёлых модулей при старте сервиса** — `_warmup_imports()` в фоновом потоке: `bsl_helpers`, `bsl_xml_parsers`, `bsl_index`, `helpers`, `openai`. Запускается перед `mcp.run()`, снижает cold start первого `rlm_start`

### Изменено
- **Оптимизация `get_callers()` COUNT** — при отсутствии `module_hint` COUNT выполняется по одной таблице `calls` (использует `idx_calls_callee`) вместо дорогого COUNT через JOIN. При наличии `module_hint` — точный COUNT через JOIN (без изменений)

### Тесты
- 6 новых тестов `prefix_recursive_ext` (распознавание + SQL + parity)
- 5 новых тестов `get_callers` (meta без hint, meta с hint, pagination, zero callers, qualified calls)

## [1.3.3] — 2026-03-20

### Добавлено
- **Агрегация хелперов в логе** — `_format_helper_summary()` группирует повторяющиеся хелперы: `code_metrics(6×, total=0.7s)` вместо 6 отдельных записей. Порядок групп — по первому появлению (dict insertion order)
- **Логирование glob fallback с причинами** — `glob_files()` логирует причину FS fallback (`reason=no_index`, `reason=unsupported`, `reason=index_error`), indexed-hit на `logger.debug`. Диагностика для выявления медленных паттернов
- **`idx_zero_callers_authoritative`** — при fresh-индексе + has_calls пустой результат `find_callers_context()` считается окончательным (без 40s+ FS fallback). Возвращает `_meta.hint` с рекомендацией `safe_grep()`. При stale/нет индекса — fallback сохраняется
- **Warmup `openai` import** — `warmup_openai_import()` с lock+flag, запускается в фоновом потоке из `_rlm_start` только при `RLM_LLM_BASE_URL`. Параллельно с построением Sandbox (~13s на медленном ПК). Без side-effect на import-time
- **Паттерн `**/Dir/**/*.ext` в whitelist** — стратегия `under_prefix_ext` в `_can_index_glob()`. Покрывает EventSubscriptions, ScheduledJobs, FunctionalOptions (`**/EventSubscriptions/**/*.xml` и т.п.)
- **Версия в описании службы** — Windows и Linux службы включают номер версии в Description (`v1.3.3`)

### Изменено
- **`make_bsl_helpers()`** — новый параметр `idx_zero_callers_authoritative: bool = False`
- **`Sandbox.__init__()`** — пробрасывает `idx_zero_callers_authoritative` в `make_bsl_helpers()`
- **`_rlm_start()`** — вычисляет `_callers_authoritative` из `IndexStatus.FRESH + has_calls`, запускает openai warmup до Sandbox
- **`simple-install.ps1`** — добавлено обновление глобального Python (как в `reinstall-service.ps1`), вывод версии

### Ожидаемый эффект на медленном ПК (ERP, 12K BSL, с индексом)

| Bottleneck | v1.3.2 | v1.3.3 |
|------------|--------|--------|
| find_callers_context FS fallback (0 callers) | 49s (timeout) | ~0s |
| sandbox / openai import на HDD | 13s | ~5-8s |
| find_event_subscriptions (без индекса) | 11.5s | ~0s |
| find_scheduled_jobs (без индекса) | 11.9s | ~0s |
| find_functional_options (без индекса) | 20.1s | ~0s |
| find_roles (без индекса) | 23.8s | ~0.1s |

### Тесты
- Было: 398 (v1.3.2)
- Стало: 418 (добавлены 20 тестов: 4 format_helper_summary, 4 glob fallback logging, 4 authoritative callers, 3 warmup, 5 under_prefix_ext)

## [1.3.2] — 2026-03-20

### Добавлено
- **Таблица `file_paths` в SQLite-индексе** — навигационный индекс всех `.bsl`/`.mdo`/`.xml` файлов. `glob_files()`, `tree()`, `find_files()` мгновенно из индекса для поддерживаемых паттернов (было ~315с на медленном ПК, стало <1с)
- **Whitelist-диспетчер `_can_index_glob()`** — безопасная трансляция ограниченного набора glob-паттернов в SQL-запросы: `**/*.ext`, `Dir/**`, `Dir/*/File.ext`, точные пути, `**/Name.ext`. Всё остальное → fallback на FS
- **`IndexReader.glob_files()`** — поиск файлов по glob-паттерну из индекса
- **`IndexReader.tree_paths()`** — получение путей для tree-рендеринга из индекса
- **`IndexReader.find_files_indexed()`** — поиск файлов по подстроке с ранжированием: exact filename > prefix > substring filename > substring path
- **Ранжирование `find_files()`** — при использовании индекса результаты ранжируются по релевантности, а не только по алфавиту
- **Hints в стратегии для файловой навигации** — описание быстрых (indexed) и медленных (FS) паттернов, рекомендация `find_module()`/`find_by_type()` вместо `glob_files()` для BSL

### Изменено
- **`builder_version = 5`** — добавлена таблица `file_paths`, расширена `index_meta` (file_paths_count)
- **`make_helpers(base_path, idx_reader=None)`** — стандартные хелперы `glob_files`/`tree`/`find_files` теперь принимают `idx_reader` через замыкание (thread-safe, без глобального состояния)
- **`Sandbox._setup_namespace()`** — передаёт `idx_reader` в `make_helpers()` для ускорения файловой навигации
- **`index info` / `index build`** — показывают `FilePaths: N` в выводе
- **Стратегия `== INDEX ==`** — включает информацию о file_paths и tips по использованию индексированных паттернов
- **`RLM_EXECUTE_DESCRIPTION`** — добавлены `search_methods`, `extract_queries`, `code_metrics`, `find_files` в описание MCP-инструмента

### Исправлено
- **`find_files_indexed()` + Кириллица** — SQLite `LOWER()` работает только с ASCII; кириллические имена файлов не находились. Убран `LOWER()` из SQL, ранжирование перенесено в Python (`str.lower()` корректно обрабатывает Unicode)
- **Рецепты `find_register_movements` / `find_register_writers`** — использовали `r['lines']`, которого нет в indexed-пути (KeyError). Теперь `r.get('lines') or r.get('source', '')` — совместимы с обоими путями
- **`_STRATEGY_IO_SECTION`** — убрано дублирование FAST/SLOW glob-паттернов (оставлено только в условной секции INDEX TIPS). Уточнена формулировка `tree('.')`: "produces too much output" вместо "Avoid" (с индексом быстро, но объём вывода чрезмерен)
- **`find_roles()` — полное имя объекта в индексе** — builder делал `rsplit(".", 1)[-1]`, сохраняя `ТестСправочник` вместо `Catalog.ТестСправочник`. Wildcard-роли (напр. `лтхБазовыеПрава` с правами на `Document.*`) терялись. Теперь хранится полное имя, reader ищет через `LIKE`
- **`find_register_movements()` — паритет index vs FS** — три исправления: (1) `code_registers` включал все source, теперь фильтруется по `source='code'`; (2) `_MANAGER_TABLE_RE` ловил вызовы `ТекстЗапросаТаблицаXxx()`, теперь только определения `Функция|Процедура ТекстЗапросаТаблицаXxx()`; (3) добавлено извлечение `adapted`-регистров из `АдаптированныйТекстЗапросаДвиженийПоРегистру` в builder и helper
- **`get_register_movements()` — SELECT DISTINCT** — убраны дубли записей (напр. `РеестрДокументов` дважды в adapted-ветке)
- **`index update` — refresh role_rights** — при инкрементальном обновлении таблица `role_rights` не обновлялась. Также исправлен early return при отсутствии BSL-дельты: теперь metadata/role_rights/file_paths обновляются всегда

### Ожидаемый эффект на медленном ПК (ERP, 12K BSL)

| Хелпер | Было (v1.3.1) | Стало (v1.3.2) |
|--------|--------------|----------------|
| glob_files(`**/*.mdo`) | 88.6с (timeout) | <0.1с |
| glob_files(Documents/*) | 65.4с (timeout) | <0.1с |
| tree(.) | 45.0с (timeout) | <1с |
| find_files() | 49.5с (timeout) | <0.1с |
| **Суммарно FS-операции** | **~315с** | **<2с** |

### Тесты
- Было: 376 (v1.3.1)
- Стало: 398 (добавлены 49 тестов file_paths + 5 тестов index/FS parity + 1 тест update role_rights refresh)

## [1.3.1] — 2026-03-19

### Добавлено
- **Fast-path startup из index_meta** — при fresh-индексе `rlm_start` восстанавливает FormatInfo и ExtensionContext из метаданных индекса, пропуская `detect_format()` и `detect_extension_context()` (43с → <1с на медленном ПК)
- **Тайминги подэтапов в rlm_start** — логирование длительности каждого подэтапа (`format`, `ext`, `overrides`, `index`, `sandbox`, `prefixes`, `strategy`) + источник данных (`index`/`disk`/`fallback`)
- **Таблица `enum_values` в SQLite-индексе** — значения перечислений с синонимами. `find_enum_values()` мгновенно из индекса (было 120с на медленном ПК)
- **Таблица `subsystem_content` в SQLite-индексе** — нормализованное хранение состава подсистем. `analyze_subsystem()` мгновенно из индекса, поиск подсистем по имени объекта (обратный lookup). Удалён бесполезный glob-паттерн `**/*{name}*.mdo`
- **`IndexReader.get_startup_meta()`** — кэшированные метаданные для быстрого старта: `source_format`, `shallow_bsl_count`, `config_role`, `extension_prefix`, `extension_purpose`
- **`IndexReader.get_enum_values()`** — поиск перечислений по имени через SQLite
- **`IndexReader.get_subsystems_for_object()`** — обратный поиск подсистем по имени объекта
- **Диагностика `find_callers_context`** — debug-логирование source (index/fallback), тайминг count_query и rows_query в `get_callers()`

### Изменено
- **`builder_version = 4`** — добавлены таблицы `enum_values`, `subsystem_content`; расширена `index_meta` (shallow_bsl_count, extension_prefix, extension_purpose, has_configuration_xml)
- **`_parse_configuration_meta()`** — дополнительно сохраняет `shallow_bsl_count`, `extension_prefix`, `extension_purpose`, `has_configuration_xml` в index_meta
- **`_rlm_start()` реструктурирован** — индекс загружается первым, затем fast path (из meta) или disk path (полное сканирование). Drift check только при disk path

### Исправлено
- **`find_enum_values()` — fallback при промахе** — если таблица `enum_values` есть, но enum не найден, возвращает ошибку мгновенно (ранее fallback на glob 11с)
- **`parse_rights_xml()` — поддержка namespace 8.2 и 8.3** — автоопределение namespace из root tag, поддержка обеих версий `http://v8.1c.ru/8.2/roles` и `http://v8.1c.ru/8.3/roles`
- **Builder ролей — переход на ElementTree** — `_parse_role_rights_for_index()` использует `parse_rights_xml()` вместо regex-парсера, который пропускал >97% записей из-за несовпадения namespace
- **`find_roles()` — дедупликация по роли** — fallback-путь группирует результаты по `role_name` и объединяет права (было 117 записей вместо 4 уникальных ролей)
- **Drift warning — корректное сравнение** — shallow vs shallow (`shallow_bsl_count` из index_meta) вместо shallow vs full. При fast path drift check пропускается

### E2E результаты (ERP, 486K методов, этот же ПК)

| Хелпер | v1.3.0 без индекса | v1.3.1 с индексом v4 |
|--------|-------------------|---------------------|
| rlm_start | 15.5с | **0.54с** |
| analyze_document_flow | 24.4с | **0.3с** |
| find_roles | 25.2с | **0.0с** |
| find_functional_options | 21.2с | **0.0с** |
| find_enum_values (hit) | 11.7с | **0.0с** |
| find_enum_values (miss) | 11.6с | **0.0с** |
| analyze_subsystem | 10.6с | **0.0с** |
| **Полная сессия (3 calls)** | ~120с | **~2.8с** |

## [1.3.0] — 2026-03-19

### Добавлено
- **Таблица `role_rights` в SQLite-индексе** — нормализованное хранение прав ролей. Regex-парсинг `Rights.xml` (CF) и `*.rights` (EDT). Параллельная индексация через `ThreadPoolExecutor` совместно с BSL-модулями.
- **Таблица `register_movements` в SQLite-индексе** — движения документов по регистрам из трёх источников: `erp_mechanism` (МеханизмыДокумента), `manager_table` (ТекстЗапросаТаблицаXxx), `code` (Движения.Xxx). `NamedTuple` для in-band данных.
- **`detected_prefixes` в `index_meta`** — при сборке индекса определяются кастомные префиксы расширений, сохраняются в метаданные. `IndexReader` возвращает их в `get_statistics()`, `rlm_start` подхватывает из индекса.
- **`find_roles(obj_name)` — мгновенный хелпер** — поиск ролей объекта из таблицы `role_rights` с автоматическим fallback на XML-сканирование.
- **`find_register_movements(doc_name)` — мгновенный хелпер** — движения документа из таблицы `register_movements` + fallback на code-парсинг.
- **`find_register_writers(reg_name)` — мгновенный хелпер** — документы, пишущие в указанный регистр, из таблицы `register_movements`.
- **Freshness check (usable/strict)** — двухуровневая проверка свежести индекса: quick (возраст + семплирование) и strict (полный пересчёт). `_index_state` инициализируется из SQLite-таблицы `modules` вместо glob.
- **`code_metrics` single-pass** — метрики BSL-модуля вычисляются за один проход по строкам (ранее — множественные regex).
- **`safe_grep` параллельный** — `ThreadPoolExecutor` с сортировкой результатов по `(file, line)`.
- **Streamable HTTP: обработка 421 Misdirected Request** — корректный ответ вместо падения при попытке SSE-подключения к Streamable HTTP эндпоинту.

### Изменено
- **`builder_version = 3`** — новая версия формата индекса (добавлены таблицы `role_rights`, `register_movements`, поле `detected_prefixes` в `index_meta`).
- **Стратегия** — секции `== INDEX ==` и `== HELPERS ==` обновлены: `find_roles`, `find_register_movements`, `find_register_writers` указаны как INSTANT при наличии индекса.
- **`reinstall-service.ps1`** — добавлена проверка наличия `pip`; обновление глобального Python через `uv pip install` перед `uv tool install`.

### Исправлено
- **`find_custom_modifications`** — EDT resolve `.mdo` файлов, порог эвристики для определения префиксов расширений.
- **MCP SDK client timeout** — задокументировано ограничение (клиент не передаёт таймаут, используется серверный `execution_timeout_seconds`).

### Тесты
- Было: 320 (v1.2.0)
- Стало: 343 (добавлены тесты `role_rights`, `register_movements`, freshness check, single-pass metrics, parallel grep, 421 handler)

## [1.2.0] — 2026-03-18

### Добавлено
- **Прозрачное ускорение хелперов через SQLite-индекс (Этап 2)** — при наличии индекса `extract_procedures`, `find_exports`, `find_callers_context`, `find_event_subscriptions`, `find_scheduled_jobs`, `find_functional_options` работают мгновенно из SQLite с автоматическим fallback на live-парсинг.
- **Новый хелпер `search_methods(query, limit=30)`** — полнотекстовый FTS5-поиск методов по подстроке имени с BM25-ранжированием. Работает только при наличии индекса с FTS.
- **Секция `== INDEX ==` в стратегии** — при загрузке индекса `rlm_start` добавляет информацию о количестве методов/вызовов, доступных мгновенных хелперах и подсказки по оптимальному батчингу.
- **Поле `index` в ответе `rlm_start`** — JSON с loaded, methods, calls, has_fts, config_name, config_version, warnings.
- **Авто-резолв XML-путей** — `parse_object_xml('Documents/Name')` автоматически находит XML (ранее требовался полный путь `Documents/Name/Ext/Document.xml`).
- **Error hints в песочнице** — при ошибках `FileNotFoundError`, `TimeoutError`, `NameError` в sandbox добавляются подсказки HINT с рекомендацией.
- **Предупреждения о медленных хелперах** — `analyze_document_flow` и `analyze_object` помечены CAUTION в стратегии для больших конфигураций.
- **Индекс методов BSL (SQLite) — Этапы 1+1.1** — автономный модуль `bsl_index.py` + CLI `rlm-bsl-index` (команды `build`, `update`, `info`, `drop`). 7 таблиц: `modules`, `methods`, `calls`, `index_meta`, `event_subscriptions`, `scheduled_jobs`, `functional_options`. FTS5 полнотекстовый поиск.
- **Метаданные конфигурации в индексе** — при build парсится `Configuration.xml` / `.mdo`: имя, версия, поставщик, формат (CF/EDT), роль (base/extension). Флаг `--no-metadata` для пропуска Level-2 таблиц (ES/SJ/FO).
- **Единая загрузка `.env`** — модуль `_config.py` с `load_project_env()`. CLI и MCP-сервер используют одну цепочку поиска: `service.json` → user-level `.env` → CWD.

### Изменено
- `get_statistics()` в `IndexReader` — boolean-флаги `has_fts`/`has_metadata` теперь возвращаются как `bool` вместо строк `"1"`/`"0"`.
- `get_scheduled_jobs()` в `IndexReader` — фильтрация по имени выполняется через SQL `WHERE name LIKE ?` вместо Python.
- `_resolve_object_xml()` — проверка существования файла через `resolve_safe().exists()` вместо чтения файла целиком.
- `find_custom_modifications()` и `analyze_object()` — упрощены: вместо перебора путей (`for xp in [...]`) используют `parse_object_xml` с авто-резолвом.
- `json` импорт в `bsl_index.py` вынесен на уровень модуля (убраны 3 inline `import json as _json`).
- Убрана двойная сортировка путей при вычислении `paths_hash`.

### Исправлено
- Парсинг inline-комментариев в `.env` файле Windows-службой (`_load_env_file`) — `RLM_INDEX_MAX_AGE_DAYS=7 # comment` корректно парсится как `7`.
- CI: обработка `PermissionError` в `extension_detector` на Linux snap.
- CI: добавлен `pythonpath` для тестов на Linux.
- CI: `dependency-groups.dev` для совместимости `uv sync --dev`.

### Тесты
- Было: 290 (v1.1.0 + Этапы 1/1.1)
- Стало: 320 (добавлены тесты интеграции индекса: 30 тестов в `test_bsl_index_integration.py`)

## [1.1.0] — 2026-03-16

### Добавлено
- **Реестр хелперов** — `_registry` + `_reg()` внутри `make_bsl_helpers()`. Strategy text, help-рецепты и available_functions генерируются автоматически из реестра. Добавление нового хелпера = функция + `_reg()`.
- **Новый хелпер `extract_queries(path)`** — извлечение встроенных запросов 1С из BSL-модулей. Парсит `Запрос.Текст = "..."` и многострочные `|`-тексты, определяет таблицы и процедуру-владельца.
- **Новый хелпер `code_metrics(path)`** — метрики BSL-модуля: строки кода/комментариев/пустые, число процедур, средний размер, максимальная вложенность.
- **GitHub Actions CI** — автоматический прогон тестов на push/PR (Ubuntu + Windows, Python 3.10 + 3.12).
- **`LazyList` / `LazyDict`** — утилиты для thread-safe lazy init с double-check locking. Заменяют 5 копипаст boilerplate в кэшах хелперов.

### Изменено
- **XML-парсеры** вынесены в отдельный модуль `bsl_xml_parsers.py` (~770 строк). `bsl_helpers.py` стал компактнее.
- **Тестовая инфраструктура** — `tests/conftest.py` с pytest-фикстурой `bsl_env`. Базовые тесты упрощены.
- **PyPI metadata** — добавлены `authors`, `classifiers`, `keywords` в `pyproject.toml`.

### Количество тестов
- Было: 234
- Стало: 236+ (добавлены тесты для extract_queries и code_metrics)

## [1.0.0] — 2026-03-13

Первая публичная версия.

- 27 BSL-хелперов песочницы + 8 стандартных (read_file, grep, glob и т.д.)
- Поддержка CF и EDT/MDO форматов
- Дисковый кэш индекса BSL-файлов
- XML-парсеры метаданных 1С (6 типов)
- Авто-детект нетиповых префиксов из кодовой базы
- Auto-strip типов метаданных (Документ.X → X)
- Параллельный prefilter (ThreadPoolExecutor) для find_callers_context
- Авто-детект расширений 1С (extension_detector)
- OpenAI-совместимый llm_query + Anthropic fallback
- Windows/Linux системная служба
- StreamableHTTP транспорт
- Thread-safety для параллельных MCP-сессий
- 234 теста
