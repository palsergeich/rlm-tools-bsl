# Changelog

## [1.6.1] — 2026-03-31

### Исправлено

- **CF-формы: корректный путь к модулю** — `parse_form()` теперь правильно возвращает `module_path` для форм в формате CF: `Ext/Form/Module.bsl` вместо `Ext/Module.bsl`
  - Исправлены обе ветки логики: live path resolution и index-based grouping (`_group_form_rows`)
  - Обновлены тестовые фикстуры для соответствия реальной структуре CF-выгрузки (Closes #4)

## [1.6.0] — 2026-03-30

### Добавлено

- **Парсинг XML форм** — новый хелпер `parse_form(object_name, form_name='', handler='')` для извлечения обработчиков событий, команд и атрибутов форм
  - Авто-детект формата CF (`Ext/Form.xml`) и EDT (`Form.form`) по namespace
  - Grouped output по формам с `module_path` для перехода к коду модуля формы
  - Обработчики с `scope`: `form` (форменные), `ext_info` (типо-специфичные), `element` (элементные)
  - Команды формы: маппинг command → BSL-процедура (CF и EDT)
  - Атрибуты: тип, `main_table` для DynamicList, `query_text` (≤512 символов)
  - Обратный поиск: `handler='ПроцИмя'` → к какому элементу/событию привязана процедура
  - Поддержка CommonForms: `category='CommonForms'`, `object_name=form_name=ИмяФормы`
- **Таблица `form_elements`** в SQLite-индексе (Level-9, index v10)
  - Параллельный сбор: `ThreadPoolExecutor(min(os.cpu_count(), 8))` по аналогии с role_rights
  - 4 индекса: по `object_name`, `(object_name, form_name)`, `handler`, `kind`
  - Meta-ключи: `has_form_elements`, `form_elements_count`
  - Мягкий апгрейд v9→v10 через `update()`, привязка к `build_metadata`
- **IndexReader**: `get_form_elements(object_name, form_name, handler)` — raw rows query
- **Бизнес-рецепт `"события формы"`** — пошаговый рецепт анализа форм объекта
  - Алиасы: `"обработчики формы"`, `"элементы формы"`, `"кнопки формы"`
- **E2E промпт** для верификации v1.6.0 в `docs/full_analysis_prompt.md`

### Изменено

- **BUILDER_VERSION** = 10
- **WORKFLOW Step 1 DISCOVER** — добавлен `parse_form()` (для задач про формы/UI)
- **RLM_EXECUTE_DESCRIPTION** — `parse_form` в списке хелперов
- **Instant helpers** — `parse_form()` при наличии `form_elements` в индексе

### Тесты

- Добавлено 35 тестов: парсер CF/EDT, хелпер (fallback + indexed), индекс, parity, CommonForms
- Обновлены тесты `test_bsl_knowledge.py`: рецепт count=7, `_match_recipe("события формы")`

## [1.5.1] — 2026-03-29

### Добавлено

- **Универсальный поиск `search()`** — единый хелпер для broad-first discovery по методам, объектам (синонимам), областям и заголовкам модулей. Fan-out по существующим поисковикам с per-source квотой, graceful degradation при отсутствии таблиц. Параметр `scope` для фильтрации по типу источника (`methods`, `objects`, `regions`, `headers`). Browse mode для пустого query с конкретным scope

### Тесты

- Добавлено 20 тестов на `search()`: diversity, quota, scope, validation, graceful degradation, registration

## [1.5.0] — 2026-03-28

### Добавлено
- **Индексирование перехватов расширений** — таблица `extension_overrides` в SQLite-индексе (Level-8, index v9)
  - При индексировании основной конфигурации автоматически сканируются соседние расширения, перехваты связываются с исходными модулями/методами
  - `_collect_extension_overrides()` — коллектор с lookup исходного модуля по `rel_path` + fallback по `object_name`+`module_type`
  - Мягкий апгрейд: v8 индекс обновляется до v9 через `CREATE TABLE IF NOT EXISTS` при `update()`
- **IndexReader**: `get_extension_overrides()`, `get_overrides_for_path()`, `get_extension_overrides_grouped()`
- **Хелпер `get_overrides()`** — мгновенный запрос перехватов из индекса с live fallback на v8/без индекса
- **Обогащение `extract_procedures`** — поле `overridden_by` у перехваченных методов (из индекса)
- **`read_procedure(include_overrides=True)`** — дописывает тело расширенного метода с аннотацией и файловой ссылкой

### Изменено
- **Fast-path `rlm_start`** — live `detect_extension_context()` + `_auto_scan_overrides()` всегда, даже при кэшированном индексе. Перехваты в ответе `rlm_start` всегда актуальны
- **WORKFLOW Step 5** — упрощён: `get_overrides()`, `read_procedure(include_overrides=True)`, `extract_procedures.overridden_by`
- **BUILDER_VERSION** = 9

### Улучшено
- **CLI `rlm-bsl-index index build`** — выводит версию индекса (`Index: v9`) в сводке после построения
- **Документация** — требование к структуре репозитория (vanessa-bootstrap) для автодетекта расширений

### Тесты
- 24 новых теста extension_overrides (включая case-insensitive кириллица, early-exit meta), **635 всего**

## [1.4.5] — 2026-03-27

### Добавлено
- **Реестр проектов** — серверный реестр `имя → путь к исходникам 1С`, новый модуль `projects.py` с `ProjectRegistry`: CRUD, трёхуровневый resolve (exact → substring → Levenshtein), атомарная запись, `.bak`, валидация путей
- **MCP-тул `rlm_projects`** — list/add/remove/rename/update проектов в реестре
- **Параметр `project` в `rlm_start`** — альтернатива `path`, резолв имени проекта через реестр; `project_hint` для незарегистрированных путей; обработка `RegistryCorruptedError`
- **Резолв mapped drives** в `rlm_projects` для Windows-сервиса (Session 0)
- **Документация** — `docs/PROJECT_REGISTRY.md`, обновлены README, ENV_REFERENCE, `.env.example`

### Тесты
- 49 новых тестов (38 юнит + 11 интеграционных), **611 всего**, 78% покрытие

## [1.4.4] — 2026-03-27

### Исправлено
- **`service` extra опубликован в PyPI** — `pip install rlm-tools-bsl[service]` теперь корректно устанавливает `pywin32` на Windows. Ранее `service` был только в `[dependency-groups]` (локальная разработка), теперь также в `[project.optional-dependencies]`

## [1.4.3] — 2026-03-27

### Добавлено
- **Публикация в PyPI** — `pip install rlm-tools-bsl` теперь основной способ установки
- **Автоматическая публикация** — при пуше тега `v*` CI собирает wheel и публикует в PyPI через OIDC Trusted Publisher (без токенов)
- **TestPyPI workflow** — ручной запуск для проверки публикации перед релизом (`publish-testpypi.yml`)
- **PyPI-бейдж** в README

### Изменено
- **README.md** — секция «Установка из PyPI» добавлена как основной способ, установка из исходников вынесена в подсекцию
- **docs/INSTALL.md** — PyPI (pip/uv) как Вариант A, установка из исходников — Вариант B
- **pyproject.toml** — добавлены classifiers (`License`, `Operating System`), URL на Changelog
- **release.yml** — добавлен job `publish` с `pypa/gh-action-pypi-publish` и OIDC

### Инфраструктура (из коммитов после v1.4.2)
- CI: `PYTHONIOENCODING=utf-8` для бенчмарка на Windows
- Зрелость OSS-репо: гигиена, coverage-бейдж, benchmark, фикс `limit` в `search_regions`/`search_module_headers`
- CI: автоматическое создание GitHub Release при пуше тега `v*`
- Ruff линтер + форматтер, SECURITY.md, фикс сигнатуры `find_custom_modifications`
- Рефакторинг документации: README упрощён, docs разнесены по темам, CONTRIBUTING + бейджи

## [1.4.2] — 2026-03-26

### Добавлено
- **Области кода** — таблица `regions` в SQLite-индексе, `search_regions(query)` для поиска областей `#Область` по имени
- **Заголовки модулей** — таблица `module_headers` в SQLite-индексе, `search_module_headers(query)` для поиска модулей по заголовочному комментарию
- **Миграция v7→v8** — автоматический full rebuild при обнаружении старого индекса
- **Delta-cleanup** — очистка regions/module_headers при инкрементальном обновлении

### Изменено
- **`BUILDER_VERSION = 8`** — добавлены таблицы `regions` и `module_headers`
- **`get_statistics()`, `get_index_info()`, `get_strategy()` обновлены** — отражают новые таблицы и возможности

## [1.4.1] — 2026-03-25

### Добавлено
- **Поиск объектов по бизнес-имени** — `search_objects(query)` с кириллическим case-insensitive поиском через UDF `py_lower()`. Таблица `object_synonyms` (12-18K строк на ЕРП) с категорийным префиксом ("Документ: Авансовый отчет"). 4-уровневое ранжирование: exact name > prefix > synonym substring > category match
- **Метаданные индекса** — `get_index_info()` возвращает версию, конфигурацию, доступные возможности (FTS, синонимы)
- **Флаг `--no-synonyms`** — отключение сборки таблицы синонимов в CLI
- **`search_objects` в WORKFLOW** — Step 1 DISCOVER дополнен поиском по бизнес-именам с разграничением от `search_methods`
- **`search_objects` как первый шаг рецептов** — все 6 доменов бизнес-рецептов начинаются с поиска объекта по синониму
- **Версия индекса в стратегии** — `Index v7 | N synonyms → search_objects() available`

### Изменено
- **`BUILDER_VERSION = 7`** — добавлена таблица `object_synonyms` с двумя индексами
- **`get_statistics()` включает `object_synonyms`** — количество записей в таблице синонимов
- **v6→v7 миграция в `update()`** — при `has_synonyms` отсутствует в index_meta (v6 индекс), синонимы строятся по умолчанию

### Важно
- **Требуется перестроение индекса** — для работы `search_objects()` и `get_index_info()` необходимо перестроить индекс: `rlm-bsl-index index build <путь>`. При инкрементальном `update` на v6 индексе таблица `object_synonyms` создаётся автоматически

### Тесты
- 40 новых тестов: коллектор CF/EDT (8), builder (5), search_objects (10), хелперы (5), стратегия (3), EDT (2), incremental update (2), категории (4), статистика (1)
- Было: 476 (стабилизация v1.4.0), стало: **516**

## [1.4.0] — 2026-03-24

### Добавлено
- **Парсинг HTTP-сервисов** — `find_http_services(name='')` с indexed + fallback. Извлекает: имя, корневой URL, шаблоны URL, HTTP-методы, обработчики
- **Парсинг веб-сервисов (SOAP)** — `find_web_services(name='')` с indexed + fallback. Извлекает: имя, namespace, операции с параметрами, обработчики
- **Парсинг XDTO-пакетов** — `find_xdto_packages(name='')` с indexed + fallback. Метаданные для обоих форматов, типы — из EDT `.xdto`
- **Состав плана обмена** — `find_exchange_plan_content(name)` с fallback на glob + XML-парсинг. CF: `Ext/Content.xml`, EDT: inline в `.mdo`. Объекты + флаг AutoRecord. Фильтрация hint-строк от indexed glob
- **Интеграционный рецепт в стратегии** — BUSINESS RECIPE для запросов об интеграции/обмене. Пошаговый план анализа из атомарных хелперов. Alias-маршрутизация: "обмен", "синхрониз", "exchange" → "интеграция"
- **`code_hint` в рецептах** — готовый Python-сниппет для слабых моделей, инжектируется в стратегию блоком `Ready-to-use code`. Реализован для рецепта "интеграция", механизм расширяемый на все домены
- **Категории XDTOPackages и ExternalDataSources** — добавлены в `METADATA_CATEGORIES` + aliases (`ПакетXDTO`, `ВнешнийИсточникДанных`)
- **Предупреждение о версии индекса** — при загрузке индекса, собранного старой версией, rlm_start выдаёт warning с рекомендацией перестроить
- **Промпт для интеграционного анализа** — `docs/full_analysis_prompt.md` дополнен вторым промптом для E2E-теста интеграционных хелперов v1.4.0

### Исправлено
- **`find_print_forms` пропускал печатные формы ERP 2.x** — Pattern 2 (property-style: `КомандаПечати.Идентификатор`) не запускался если Pattern 1 (helper-style: `ДобавитьКомандуПечати()`) уже нашёл хотя бы одну форму. Теперь оба паттерна работают одновременно с дедупликацией по `name`. Результат на РеализацияТоваровУслуг: было 1, стало 13 печатных форм

### Изменено
- **`BUILDER_VERSION = 6`** — добавлены таблицы `http_services`, `web_services`, `xdto_packages` в SQLite-индекс
- **`get_statistics()` включает `builder_version`** — для проверки совместимости индекса
- **Шаги рецепта "интеграция" уточнены** — шаг 4 (планы обмена) теперь явно указывает получить имена через `find_by_type`, шаг 6 (рег.задания) содержит готовый Python-фильтр вместо абстрактного "filtered by"

### Важно
- **Требуется перестроение индекса** — для работы новых хелперов (`find_http_services`, `find_web_services`, `find_xdto_packages`) необходимо перестроить индекс: `rlm-bsl-index index build <путь>`. Без перестроения хелперы работают через fallback (XML-парсинг в реальном времени)

### Тесты
- 30 новых тестов: парсеры (18), рецепты (9), категории (4), version (2)
- Было: 441 (v1.3.5), стало: **471**
- E2E верификация: EDT с индексом (ЕРП 2.5), CF с индексом (ЕРП 2.5.14), CF без индекса (БГУ). Агенты Sonnet + Cursor — 0 регрессий

## [Unreleased]

### Исправлено
- **`index update` — миграция интеграционных таблиц** — при обновлении индекса, собранного до v1.4.0, таблицы `http_services`, `web_services`, `xdto_packages` и их индексы создаются автоматически (`CREATE TABLE IF NOT EXISTS`). Ранее `_insert_metadata_tables()` падала с `OperationalError` на старом индексе без этих таблиц
- **`find_xdto_packages()` fallback — защита от отсутствия `Package.xdto`** — для EDT-пакетов `.mdo` может существовать без рядом лежащего `Package.xdto`. Ранее `read_file_fn()` выбрасывал `FileNotFoundError`, теперь пакет возвращается с пустым `types`
- **`save_config()` / `install()` / `uninstall()` — консистентный путь конфига** — `save_config()` писал в хардкод `CONFIG_FILE`, игнорируя `RLM_CONFIG_FILE`. Windows install прошивал в реестр тот же хардкод, `uninstall()` удалял его. Теперь все пути используют `_config_path()`, который учитывает override через `RLM_CONFIG_FILE`
- **`find_callers_context()` — унификация `_meta`** — fallback возвращал `{total_files, scanned_files, has_more}`, indexed путь — `{total_callers, returned, offset, has_more}`. Теперь оба пути возвращают единый контракт `{total_callers, returned, offset, has_more}`
- **`find_roles()` fallback — поле `object` в результате** — indexed путь включал `"object"` в каждый role-item, fallback при группировке терял это поле. Добавлено `"object": object_name` в grouped dict
- **`index update` — пересчёт `detected_prefixes`** — при полном `build()` кастомные префиксы пересчитывались и записывались в `index_meta`. В `update()` этот шаг отсутствовал — после инкрементального обновления префиксы оставались устаревшими. Добавлен `_detect_prefixes()` + запись в `index_meta` в конце `update()`
- **Утечка MCP transport-сессий** — включён `stateless_http=True` в FastMCP. Ранее каждый HTTP-запрос без заголовка `Mcp-Session-Id` создавал transport-сессию, которая оставалась в памяти навсегда (клиенты не шлют DELETE при отключении). Накопление сессий приводило к `WinError 10055` (исчерпание сокетов Windows) и падению службы
- **Health check создавал MCP-сессии** — watchdog делал `POST /mcp` с JSON-RPC телом каждые 30 сек, что создавало лишнюю transport-сессию при каждой проверке. Добавлен лёгкий `GET /health` endpoint (`{"status": "ok"}`), watchdog и `reinstall-service.ps1` переведены на него
- **Предупреждение PowerShell при Invoke-WebRequest** — добавлен `-UseBasicParsing` в verify-шаге `reinstall-service.ps1`

### Добавлено
- **Лог режима при старте** — `transport=streamable-http stateless_http=True host=... port=...` в server.log для диагностики
- **Лог первого успешного health check** — watchdog пишет `Health check OK (url)` один раз после старта
- **Фильтр `GET /health` в uvicorn access log** — `_HealthLogFilter` подавляет шум от health check каждые 30 сек

### Документация
- Актуализирован подсчёт хелперов: 28 BSL + 8 I/O + 2 LLM = **38** (было указано 29)
- Добавлены `grep_read` и `search_methods` в docs/HELPERS.md
- Описание `index update` в INDEXING.md дополнено: schema upgrade и пересчёт `detected_prefixes`

### Тесты
- 5 новых тестов стабилизации: миграция integration tables, пересчёт prefixes, XDTO без Package.xdto, save_config override (2 теста)
- Обновлены 3 теста `find_callers_context`: `_meta` → новый контракт `{total_callers, returned, offset, has_more}`
- Расширен тест `find_roles`: проверка наличия поля `object` в каждом role-item
- E2E верификация: EDT+index (ЕРП 2.5), CF+index (ЕРП 2.5.14) — 0 регрессий, метрики совпадают с baseline v1.4.0
- Было: 471 (v1.4.0), стало: **476**

## [1.3.5] — 2026-03-23

### Добавлено
- **Бизнес-рецепты в стратегии** — `_BUSINESS_RECIPES` dict с 5 доменами (себестоимость, проведение, распределение, печать, права). Каждый домен содержит `compact` (3-4 шага) и `full` (6-7 шагов с альтернативами) план анализа
- **Step 0 — UNDERSTAND в WORKFLOW** — новый шаг перед DISCOVER: подсказка агенту декодировать бизнес-вопрос и найти рецепт
- **Динамическая инъекция рецепта в `get_strategy()`** — новый параметр `query`, матчинг по доменным ключевым словам через `_match_recipe()`. Уровень детализации: `compact` при low/medium, `full` при high/max. Без совпадения — только generic Step 0
- **Прокидывание `query` из `server.py`** — текст пользовательского запроса из `rlm_start` передаётся в `get_strategy()` для выбора релевантного рецепта
- **Логирование символов и токенов MCP-трафика** — `rlm_start` и `rlm_execute` пишут `out_chars` / `out_tokens~` в лог, `rlm_end` выдаёт итог сессии: `in_chars`, `out_chars`, `total_chars`, `total_tokens~` (оценка: chars / 1.75 для смешанного кириллица+код)

### Тесты
- 12 новых тестов бизнес-рецептов: структура dict, `_match_recipe()`, compact/full инъекция, case-insensitive, all domains
- Было: 429 (v1.3.4), стало: **441**

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
- **`find_roles()` — полное имя объекта в индексе** — builder делал `rsplit(".", 1)[-1]`, сохраняя `ТестСправочник` вместо `Catalog.ТестСправочник`. Wildcard-роли (напр. `кст_БазовыеПрава` с правами на `Document.*`) терялись. Теперь хранится полное имя, reader ищет через `LIKE`
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
