# Changelog

## [Unreleased]

### Добавлено
- **Индекс методов BSL (SQLite)** — автономный модуль `bsl_index.py` + CLI `rlm-bsl-index` (команды `build`, `update`, `info`, `drop`). 7 таблиц: `modules`, `methods`, `calls`, `index_meta`, `event_subscriptions`, `scheduled_jobs`, `functional_options`.
- **Метаданные конфигурации в индексе** — при build парсится `Configuration.xml` / `.mdo`: имя, версия, поставщик, формат (CF/EDT), роль (base/extension). Флаг `--no-metadata` для пропуска Level-2 таблиц (ES/SJ/FO).
- **Единая загрузка `.env`** — модуль `_config.py` с `load_project_env()`. CLI и MCP-сервер используют одну цепочку поиска: `service.json` → user-level `.env` → CWD. Команда `rlm-bsl-index` работает из любого каталога.

### Изменено
- **Секция «Оптимизации»** перенесена из README в `docs/HELPERS.md`
- **README** — добавлена информация о файле конфигурации сервиса (`service.json`)

### Исправлено
- CI: обработка `PermissionError` в `extension_detector` на Linux snap
- CI: добавлен `pythonpath` для тестов на Linux
- CI: `dependency-groups.dev` для совместимости `uv sync --dev`

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
