# Changelog

## [Unreleased]

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
