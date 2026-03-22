# Индекс методов BSL

## 1. Что такое индекс методов

Индекс методов — это предварительно построенная SQLite-база, содержащая таблицу всех процедур и функций кодовой базы 1С, а также (опционально) эвристический граф вызовов между ними. Индекс позволяет мгновенно отвечать на вопросы типа "какие методы есть в модуле?", "кто вызывает эту функцию?", "какие экспортные процедуры у объекта?" — без повторного чтения файлов с диска.

### Аналоги и сравнение

| Возможность | CodeRLM | Aider RepoMap | GitHub BM25 | **rlm-tools-bsl** |
|---|---|---|---|---|
| Таблица символов (методы/функции) | tree-sitter → JSON API | ctags / tree-sitter | — | regex-парсинг → SQLite `methods` (569K методов ERP) |
| Граф вызовов | нет | «файл → символ» + PageRank | нет | эвристический regex → SQLite `calls` (4.5M рёбер ERP). PageRank — Future, данные есть |
| Полнотекстовый поиск | нет | нет | BM25 (Elasticsearch) | FTS5 trigram + BM25, встроен в SQLite (`search_methods`) |
| Метаданные конфигурации | нет | нет | нет | `index_meta` (имя, версия, формат, роль), Level-2: ES/SJ/FO (3 таблицы) |
| Прозрачная интеграция | отдельный API | карта для промпта | отдельный сервис | хелперы автоматически ускоряются при наличии индекса, fallback на live-парсинг |
| Инкрементальное обновление | нет | полная перестройка | нет | `index update` — только изменённые/новые/удалённые файлы (10–20с vs 6 мин full build) |
| Зависимости | Rust + tree-sitter | tree-sitter / ctags + networkx | Elasticsearch | чистый Python + SQLite (stdlib), ноль внешних зависимостей |
| Целевой язык | 10+ языков | 10+ языков | любой | заточен под BSL/1С (regex-грамматика, CF/EDT форматы, XML-метаданные) |

**CodeRLM** — Rust-сервер с tree-sitter, строит таблицу символов. У нас аналогичная идея, но без tree-sitter (нет грамматики для BSL), парсим regex-ом.

**Aider RepoMap** — граф «файл → символ» с PageRank для ранжирования важности. У нас таблица `calls` — тот же граф, но в SQL. PageRank пока не делаем, данные для него уже есть.

**BM25 / keyword search** — GitHub выбрал BM25 вместо векторных эмбеддингов для поиска по коду. У нас FTS5 с trigram-токенайзером — подстроковый поиск по именам методов с BM25-ранжированием, встроенный в SQLite.

**Чем отличаемся:** Без tree-sitter, networkx, embeddings или внешних зависимостей. Чистый Python + SQLite (stdlib). Заточен под BSL/1C. Индекс полностью опциональный — без него всё работает, с ним — мгновенно. E2E-тесты показали снижение MCP-вызовов на 33% (Sonnet) и кратное улучшение качества для слабых моделей (Minimax: с ошибок до чистого отчёта).

## 2. Включение и настройка

Признак «включённости» индекса — не переменная окружения, а наличие файла `method_index.db` в каталоге хранения. Индекс создаётся командой `rlm-bsl-index index build <path>`. Если файл `method_index.db` существует — хелперы автоматически используют его для ускорения. Если файла нет — всё работает как раньше, без индекса.

Поведение индекса настраивается переменными окружения:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `RLM_INDEX_DIR` | `~/.cache/rlm-tools-bsl/` | Каталог хранения индексов (Linux: `/home/user/.cache/rlm-tools-bsl/`, Windows: `C:\Users\user\.cache\rlm-tools-bsl\`). Внутри создаётся подкаталог с хешем пути конфигурации, например: `C:\Users\user\.cache\rlm-tools-bsl\a3f8b2c1d4e5\method_index.db` |
| `RLM_INDEX_MAX_AGE_DAYS` | `7` | Порог предупреждения о возрасте индекса (дни). Если индекс старше — статус `STALE_AGE` |
| `RLM_INDEX_SAMPLE_SIZE` | `5` | Количество файлов для выборочной проверки свежести. `0` — отключить проверку |
| `RLM_INDEX_SAMPLE_THRESHOLD` | `30` | Минимальное число модулей в индексе, при котором выполняется выборочная проверка |
| `RLM_INDEX_SKIP_SAMPLE_HOURS` | `24` | Если индекс моложе этого порога (часы), выборочная проверка пропускается |

Переменные можно задать в `.env` файле или в окружении системы.

## 3. CLI-команды

### Полное построение

```bash
rlm-bsl-index index build <path> [--no-calls] [--no-metadata] [--no-fts]
```

Сканирует все `.bsl`-файлы в `<path>`, извлекает процедуры/функции и (по умолчанию) эвристический граф вызовов. Дополнительно парсит XML-метаданные конфигурации и строит FTS5-индекс для полнотекстового поиска.

Флаги:
- `--no-calls` — отключает построение графа вызовов (значительно ускоряет сборку)
- `--no-metadata` — отключает парсинг таблиц метаданных (EventSubscriptions, ScheduledJobs, FunctionalOptions)
- `--no-fts` — отключает построение FTS5-индекса полнотекстового поиска

Пример:

```
$ rlm-bsl-index index build "D:\ERP\src\cf"
Building index for: D:\ERP\src\cf
Call graph:  yes
Metadata:    yes
FTS search:  yes

Index built in 390.9s
  Config:   УправлениеПредприятием 2.5.14.59
  Format:   cf
  Modules:  23461
  Methods:  569068
  Calls:    4554311
  Exports:  187585
  EventSubs:  536
  SchedJobs:  238
  FuncOpts:   929
  FilePaths:  103128
  DB size:  1117.8 MB
  DB path:  C:\Users\user\.cache\rlm-tools-bsl\a1b2c3d4e5f6\method_index.db
```

### Инкрементальное обновление

```bash
rlm-bsl-index index update <path>
```

Обновляет только изменённые файлы (по mtime+size). Требует предварительно построенного индекса.

Пример:

```bash
$ rlm-bsl-index index update D:\ERP\src
Инкрементальное обновление: D:\ERP\src

Обновлено за 1.2 сек
  Добавлено: 0
  Изменено:  3
  Удалено:   0
```

### Информация и статус

```bash
rlm-bsl-index index info <path>
```

Показывает статистику индекса и проверяет его актуальность.

Пример:

```
$ rlm-bsl-index index info "D:\ERP\src\cf"
Index: C:\Users\user\.cache\rlm-tools-bsl\a1b2c3d4e5f6\method_index.db
  Config:   УправлениеПредприятием 2.5.14.59
  Format:   cf
  Status:   fresh
  Modules:  23461
  Methods:  569068
  Calls:    4554311
  Exports:  187585
  EventSubs:  536
  SchedJobs:  238
  FuncOpts:   929
  FilePaths:  103128
  FTS:      yes
  DB size:  1117.8 MB
  Built:    10s ago
  BSL files on disk: 23461
```

Возможные статусы: `fresh`, `stale (structure changed)`, `stale (age)`, `stale (content)`, `missing`.

### Удаление индекса

```bash
rlm-bsl-index index drop <path>
```

Удаляет файл индекса и (если пустой) его каталог.

Пример:

```bash
$ rlm-bsl-index index drop D:\ERP\src
Индекс удалён: C:\Users\user\.cache\rlm-tools-bsl\a1b2c3d4e5f6\method_index.db (142.5 MB)
```

## 4. Структура индекса

Индекс хранится в SQLite-базе `method_index.db` и содержит 12 таблиц (4 основные + 7 метаданных + 1 навигационная) + виртуальную FTS5-таблицу для полнотекстового поиска:

### index_meta

Служебная key-value таблица для проверки свежести, совместимости и идентификации конфигурации.

| key | Описание | Пример |
|-----|----------|--------|
| `version` | Версия схемы | `5` |
| `builder_version` | Версия построителя | `5` |
| `bsl_count` | Количество .bsl файлов | `23461` |
| `paths_hash` | MD5-хеш отсортированных путей | `6c4e5a0f0d506f67...` |
| `built_at` | Unix-timestamp построения | `1773740509.56` |
| `base_path` | Исходный каталог конфигурации | `D:\ERP\src\cf` |
| `has_calls` | Есть ли граф вызовов (0/1) | `1` |
| `has_metadata` | Есть ли таблицы метаданных (0/1) | `1` |
| `has_fts` | Есть ли FTS5-индекс полнотекстового поиска (0/1) | `1` |
| `config_name` | Имя конфигурации из Configuration.xml/.mdo | `УправлениеПредприятием` |
| `config_synonym` | Синоним конфигурации | `1С:ERP Управление предприятием 2` |
| `config_version` | Версия конфигурации | `2.5.20.80` |
| `config_vendor` | Поставщик | `Фирма "1С"` |
| `source_format` | Формат исходников | `cf` или `edt` |
| `config_role` | Роль конфигурации | `base` или `extension` |
| `shallow_bsl_count` | Количество .bsl файлов (depth≤4, для fast-path startup) | `12036` |
| `extension_prefix` | Префикс расширения (NamePrefix из XML) | `мое_` |
| `extension_purpose` | Назначение расширения | `Customization` |
| `has_configuration_xml` | Наличие Configuration.xml (0/1) | `0` |
| `detected_prefixes` | JSON-массив авто-определённых кастомных префиксов | `["ал"]` |
| `file_paths_count` | Количество записей в таблице file_paths | `35000` |

### modules

По одной записи на каждый `.bsl`-файл конфигурации.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `7682` |
| `rel_path` | TEXT UNIQUE | Относительный путь от корня конфигурации | `CommonModules/ПользователиКлиент/Ext/Module.bsl` |
| `category` | TEXT | Категория объекта метаданных | `CommonModules`, `Documents`, `Catalogs` |
| `object_name` | TEXT | Имя объекта | `ПользователиКлиент` |
| `module_type` | TEXT | Тип модуля | `Module`, `ObjectModule`, `ManagerModule` |
| `form_name` | TEXT | Имя формы (для модулей форм) | `ФормаДокумента` или NULL |
| `is_form` | INTEGER | Флаг: модуль формы (0/1) | `0` |
| `mtime` | REAL | os.path.getmtime() — для инкрементального update | `1773653983.0` |
| `size` | INTEGER | os.path.getsize() в байтах | `3064` |

### methods

Все процедуры и функции всех модулей.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `388066` |
| `module_id` | INTEGER FK → modules | Ссылка на модуль | `7682` |
| `name` | TEXT | Имя процедуры/функции | `УстановитьУсловноеОформление` |
| `type` | TEXT | `Процедура` или `Функция` | `Процедура` |
| `is_export` | INTEGER | Флаг экспорта (0/1) | `1` |
| `params` | TEXT | Строка параметров из определения | `Отказ, СтандартнаяОбработка` |
| `line` | INTEGER | Номер строки начала | `565` |
| `end_line` | INTEGER | Номер строки конца | `568` |
| `loc` | INTEGER | Количество строк (end_line - line + 1) | `4` |

### calls

Эвристический граф вызовов (regex-based). Заполняется только при построении без `--no-calls`.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `2376925` |
| `caller_id` | INTEGER FK → methods | Кто вызывает (ссылка на methods) | `388066` |
| `callee_name` | TEXT | Кого вызывает (имя или Модуль.Метод) | `НоменклатураСервер.УстановитьУсловноеОформлениеЕдиницИзмерения` |
| `line` | INTEGER | Номер строки вызова в исходном файле | `1135` |

Направление чтения: **caller → callee** (кто → кого). Квалифицированные вызовы (`Модуль.Метод`) хранятся as-is без разрешения — `Модуль` может быть как общим модулем, так и локальной переменной (см. раздел 7).

### event_subscriptions

Подписки на события конфигурации. Заполняется при построении без `--no-metadata`. Парсинг XML: `EventSubscriptions/**/*.xml` и `*.mdo`.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `name` | TEXT | Имя подписки | `ВариантОтчетаПередУдалением` |
| `synonym` | TEXT | Синоним | `Вариант отчета перед удалением` |
| `event` | TEXT | Тип события | `BeforeWrite`, `OnWrite`, `Posting`, `BeforeDelete` |
| `handler_module` | TEXT | Модуль-обработчик | `ВариантыОтчетов` |
| `handler_procedure` | TEXT | Процедура-обработчик | `ПередУдалениемИдентификатора` |
| `source_types` | TEXT | JSON-массив типов-источников | `["DocumentObject.РеализацияТоваровУслуг"]` |
| `source_count` | INTEGER | Количество типов-источников (0 = catch-all) | `2` |
| `file` | TEXT | Относительный путь к XML | `EventSubscriptions/Имя.xml` |

Ускоряет хелперы: `find_event_subscriptions()`, `analyze_document_flow()`.

### scheduled_jobs

Регламентные (фоновые) задания. Заполняется при построении без `--no-metadata`. Парсинг XML: `ScheduledJobs/**/*.xml` и `*.mdo`.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `name` | TEXT | Имя задания | `АрхивированиеЧековККМ` |
| `synonym` | TEXT | Синоним | `Архивирование чеков ККМ` |
| `method_name` | TEXT | Полное имя метода | `CommonModule.РозничныеПродажи.АрхивированиеЧековККМ` |
| `handler_module` | TEXT | Модуль-обработчик | `РозничныеПродажи` |
| `handler_procedure` | TEXT | Процедура-обработчик | `АрхивированиеЧековККМ` |
| `use` | INTEGER | Включено (0/1) | `1` |
| `predefined` | INTEGER | Предопределённое (0/1) | `1` |
| `restart_count` | INTEGER | Количество перезапусков при ошибке | `3` |
| `restart_interval` | INTEGER | Интервал перезапуска (сек) | `60` |
| `file` | TEXT | Относительный путь к XML | `ScheduledJobs/Имя.xml` |

Ускоряет хелперы: `find_scheduled_jobs()`, `analyze_document_flow()`.

### functional_options

Функциональные опции конфигурации. Заполняется при построении без `--no-metadata`. Парсинг XML: `FunctionalOptions/**/*.xml` и `*.mdo`.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `name` | TEXT | Имя опции | `ИспользоватьСкладскойУчет` |
| `synonym` | TEXT | Синоним | `Использовать складской учёт` |
| `location` | TEXT | Хранение (константа/ресурс регистра) | `Constant.ИспользоватьСкладскойУчет` |
| `content` | TEXT | JSON-массив объектов, на которые влияет | `["Document.Заказ", "Catalog.Склады"]` |
| `file` | TEXT | Относительный путь к XML | `FunctionalOptions/Имя.xml` |

Ускоряет хелперы: `find_functional_options()`.

### enum_values

Значения перечислений конфигурации. Парсинг XML: `Enums/**/*.xml` и `*.mdo`.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `name` | TEXT | Имя перечисления | `ХозяйственныеОперации` |
| `synonym` | TEXT | Синоним | `Хозяйственные операции` |
| `values_json` | TEXT | JSON-массив значений [{name, synonym}] | `[{"name": "Продажа", "synonym": "Продажа"}]` |
| `source_file` | TEXT | Относительный путь к XML | `Enums/ХозяйственныеОперации/ХозяйственныеОперации.mdo` |

Ускоряет хелперы: `find_enum_values()`.

### subsystem_content

Состав подсистем (нормализованная таблица, одна строка на пару подсистема-объект). Парсинг XML: `Subsystems/**/*.xml` и `*.mdo`.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `subsystem_name` | TEXT | Имя подсистемы | `ОбъектыУТКАУП` |
| `subsystem_synonym` | TEXT | Синоним подсистемы | `Объекты УТ, КА, УП` |
| `object_ref` | TEXT | Ссылка на объект (Category.Name) | `Document.ПоступлениеТоваровНаСклад` |
| `file` | TEXT | Относительный путь к XML | `Subsystems/ОбъектыУТКАУП/ОбъектыУТКАУП.mdo` |

Ускоряет хелперы: `analyze_subsystem()`. Поддерживает обратный поиск: какие подсистемы содержат указанный объект.

### role_rights

Нормализованное хранение прав ролей. Парсинг XML: `Roles/**/Rights.xml` (CF) и `*.rights` (EDT) через `parse_rights_xml` (ElementTree, поддержка namespace 8.2/8.3).

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `role_name` | TEXT | Имя роли | `ДобавлениеИзменениеДокументов` |
| `object_name` | TEXT | Полное имя объекта (Category.Name) | `Document.РеализацияТоваровУслуг` |
| `right_name` | TEXT | Имя права | `Read`, `Insert`, `Posting` |
| `file` | TEXT | Относительный путь к XML | `Roles/Имя/Ext/Rights.xml` |

Хранятся только granted-права (`<value>true</value>`). Объект хранится с полным именем (напр. `Document.РеализацияТоваровУслуг`), reader ищет через `LIKE '%name%'` — поиск и по полному, и по короткому имени.

Ускоряет хелперы: `find_roles()`.

### register_movements

Движения документов по регистрам. Извлекаются in-band при обработке BSL-файлов документов (без дополнительного I/O).

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `document_name` | TEXT | Имя документа | `РеализацияТоваровУслуг` |
| `register_name` | TEXT | Имя регистра | `ТоварыНаСкладах` |
| `source` | TEXT | Источник | `code`, `erp_mechanism`, `manager_table`, `adapted` |
| `file` | TEXT | Относительный путь к BSL | `Documents/Имя/Ext/ObjectModule.bsl` |

Четыре типа source:
- `code` — прямые обращения `Движения.RegName` в ObjectModule
- `erp_mechanism` — `МеханизмыДокумента.Добавить("RegName")` в ManagerModule
- `manager_table` — определения `Функция ТекстЗапросаТаблицаRegName()` в ManagerModule
- `adapted` — `ИмяРегистра = "RegName"` внутри `АдаптированныйТекстЗапросаДвиженийПоРегистру` в ManagerModule

Ускоряет хелперы: `find_register_movements()`, `find_register_writers()`, `analyze_document_flow()`.

### file_paths

Навигационный индекс файлов (.bsl/.mdo/.xml) для ускорения `glob_files()`, `tree()`, `find_files()`.

| Колонка | Тип | Описание | Пример |
|---------|-----|----------|--------|
| `id` | INTEGER PK | Идентификатор | `1` |
| `rel_path` | TEXT UNIQUE | POSIX-путь от корня конфигурации | `Documents/МойДокумент/ObjectModule.bsl` |
| `extension` | TEXT | Расширение файла | `.bsl`, `.mdo`, `.xml` |
| `dir_path` | TEXT | Директория файла | `Documents/МойДокумент` |
| `filename` | TEXT | Имя файла | `ObjectModule.bsl` |
| `depth` | INTEGER | Количество сегментов в пути | `3` |
| `size` | INTEGER | Размер файла в байтах | `3064` |
| `mtime` | REAL | Время модификации | `1773653983.0` |

При `update()` таблица пересобирается целиком (DROP + INSERT). Причина: .mdo/.xml меняются крайне редко, full rebuild дешёвый (~1-2с для 30-50K записей).

**Поддерживаемые glob-паттерны (indexed, мгновенные):**

| Паттерн | SQL-стратегия |
|---------|---------------|
| `**/*.ext` | `WHERE extension = '.ext'` |
| `**/Dir/**/*.ext` | `WHERE dir_path LIKE '%/Dir/%' AND extension = '.ext'` |
| `Dir/**/*.ext` | `WHERE rel_path LIKE 'Dir/%' AND extension = '.ext'` |
| `Dir/*/File.ext` | `WHERE dir_path LIKE 'Dir/%' AND filename = 'File.ext'` |
| `Dir/**` или `Dir/**/*` | `WHERE rel_path LIKE 'Dir/%'` |
| Точный путь | `WHERE rel_path = 'path'` |
| `**/Name.ext` | `WHERE filename LIKE 'Name%' AND extension = '.ext'` |

Все остальные паттерны → fallback на FS (`pathlib.Path.glob()`).

### methods_fts (FTS5)

Виртуальная таблица полнотекстового поиска методов. Строится по умолчанию, отключается флагом `--no-fts`. Использует trigram-токенайзер — разбивает имена на тройки символов для подстрокового поиска с BM25-ранжированием.

```sql
CREATE VIRTUAL TABLE methods_fts USING fts5(name, object_name, tokenize='trigram');
```

Данные копируются из `methods` + `modules` (имя метода + имя объекта-владельца). Поиск:

```sql
SELECT m.name, mod.object_name, methods_fts.rank
FROM methods_fts
JOIN methods m ON m.id = methods_fts.rowid
JOIN modules mod ON mod.id = m.module_id
WHERE methods_fts MATCH '"Документ"'
ORDER BY methods_fts.rank
LIMIT 30;
```

**Пример результата** (поиск "Документ"):

| name | object_name | rank |
|------|-------------|------|
| ОбработкаПроведенияДокументов | ДокументыМенеджер | -3.82 |
| ПолучитьСписокДокументов | ОбщегоНазначения | -2.41 |

Значение `rank` — BM25 score (отрицательное, ближе к 0 = менее релевантно).

**Размер:** +232 MB на ERP (~569K методов с длинными русскими CamelCase-именами). **Время построения:** +5 секунд к общему build. Отключение: `--no-fts`.

## 5. Инкрементальное обновление

Команда `index update` работает по следующему алгоритму:

1. Сканирует текущие `.bsl`-файлы на диске
2. Сравнивает `mtime` и `size` каждого файла с сохранёнными значениями в таблице `modules`
3. Определяет дельту: добавленные (новые файлы), изменённые (расхождение mtime/size), удалённые (файл исчез с диска)
4. Обновляет только затронутые файлы в одной транзакции: удаляет старые записи из `modules`, `methods`, `calls`, затем вставляет новые
5. Неизменённые файлы (как правило, 99%+ кодовой базы) не читаются с диска вообще

На практике инкрементальное обновление ERP (~24K файлов) после изменения нескольких модулей занимает 1-3 секунды.

## 6. Проверка свежести (Staleness Detection)

Проверка свежести выполняется на двух уровнях:

### Quick check (rlm_start)

При старте сессии (`rlm_start`) используется облегчённая проверка `check_index_usable`, которая **не выполняет rglob по файловой системе** — это критично для больших конфигураций и сетевых дисков:

1. **Проверка возраста** (`RLM_INDEX_MAX_AGE_DAYS`) — если индекс старше порога, статус `STALE_AGE`
2. **Пропуск для молодых индексов** (`RLM_INDEX_SKIP_SAMPLE_HOURS`) — если индекс моложе порога, сразу `FRESH`
3. **Выборочная проверка содержимого** — из индекса случайно выбирается `RLM_INDEX_SAMPLE_SIZE` файлов (по умолчанию 5), stat()-вызовы выполняются параллельно. Если более 20% выборки не совпадает, статус `STALE_CONTENT`

Дополнительно (только при disk path): `rlm_start` сравнивает `bsl_file_count` из `detect_format()` с `shallow_bsl_count` из `index_meta` (одинаковая методика подсчёта, depth≤4). Расхождение >5% добавляет предупреждение. При fast-path startup (fresh index) drift check пропускается — обе метрики из одного источника.

В ответе `rlm_start` поле `"index_check": "quick"` — явный сигнал, что полная структурная проверка не выполнялась.

### Strict check (CLI `index info`)

Команда `rlm-bsl-index index info` выполняет полную проверку `check_index_strict`:

1. **Структурная проверка** (`bsl_count` + `paths_hash`) — если количество файлов изменилось или набор путей не совпадает (файлы добавлены/удалены), статус `STALE`
2. **Проверка возраста** (`RLM_INDEX_MAX_AGE_DAYS`) — если индекс старше порога, статус `STALE_AGE`
3. **Выборочная проверка содержимого** — аналогично quick check

### Рекомендуемая стратегия обновления

Для проектов средней загруженности (несколько разработчиков, ежедневные изменения):

- **После получения обновлений (git pull):** `rlm-bsl-index index update <path>` (1-3с)
- **Раз в неделю или после крупных изменений:** `rlm-bsl-index index build <path>` (полная перестройка)
- **Полная проверка состояния:** `rlm-bsl-index index info <path>`

### MCP SDK: рекомендуемый client timeout

При запуске `rlm_start` на очень больших конфигурациях (>20K BSL-файлов) время инициализации может превышать 30 секунд, особенно на медленных дисках или сетевых ресурсах. Если MCP-клиент устанавливает timeout ниже этого значения, может произойти ошибка `AssertionError: Request already responded to` в MCP SDK — клиент отправляет timeout-ответ, а сервер пытается ответить после завершения обработки.

**Рекомендация:** установить client timeout не менее 60 секунд. С оптимизациями v1.3.1 (fast-path startup из index_meta) время `rlm_start` сокращается до <1 секунды при наличии fresh-индекса, 10-15 секунд без индекса.

Если проверка выявила устаревание, агент получает предупреждение с рекомендацией запустить `index update` или `index build`.

## 7. Интеграция в хелперы (прозрачное ускорение)

При запуске `rlm_start` система автоматически проверяет наличие и актуальность индекса. Если индекс найден и пригоден к использованию (`FRESH`, `STALE_AGE` или `STALE_CONTENT`), он подключается к хелперам через `IndexReader`. Если индекса нет — хелперы работают как раньше (live-парсинг .bsl и XML).

### Ускоряемые хелперы

| Хелпер | С индексом | Без индекса |
|--------|-----------|-------------|
| `extract_procedures(path)` | `SELECT` из `methods` (мгновенно) | Regex-парсинг .bsl |
| `find_exports(path)` | `SELECT WHERE is_export=1` (мгновенно) | Фильтр `extract_procedures` |
| `find_callers_context(proc)` | `JOIN calls+methods+modules` (мгновенно) | Параллельный scan+grep .bsl |
| `find_event_subscriptions(obj)` | `SELECT` из `event_subscriptions` (мгновенно) | XML-парсинг `EventSubscriptions/**` |
| `find_scheduled_jobs(name)` | `SELECT` из `scheduled_jobs` (мгновенно) | XML-парсинг `ScheduledJobs/**` |
| `find_functional_options(obj)` | `SELECT` из `functional_options` (мгновенно) | XML-парсинг `FunctionalOptions/**` |
| `find_enum_values(name)` | `SELECT` из `enum_values` (мгновенно) | Glob + XML-парсинг `Enums/**` |
| `analyze_subsystem(name)` | `SELECT` из `subsystem_content` (мгновенно) | Glob + XML-парсинг `Subsystems/**` |
| `find_roles(obj)` | `SELECT` из `role_rights` (мгновенно) | Парсинг Rights.xml / .rights |
| `find_register_movements(doc)` | `SELECT DISTINCT` из `register_movements` (мгновенно) | Grep по ObjectModule + ManagerModule |
| `find_register_writers(reg)` | `SELECT` из `register_movements` (мгновенно) | Параллельный поиск по ObjectModule |
| `glob_files(pattern)` | `SELECT` из `file_paths` (мгновенно, для поддерживаемых паттернов) | `pathlib.Path.glob()` — полный обход FS |
| `tree(path)` | `SELECT` из `file_paths` + Python-форматирование (мгновенно) | Рекурсивный `iterdir()` |
| `find_files(name)` | `SELECT` из `file_paths` с ранжированием (мгновенно) | `os.walk()` + in-memory поиск |
| `search_methods(query)` | FTS5 (BM25) (мгновенно) | Недоступен |

### search_methods — полнотекстовый поиск

```python
search_methods(query, limit=30) -> list[dict]
```

Полнотекстовый поиск методов по подстроке имени через FTS5-индекс. Возвращает результаты, отсортированные по BM25-релевантности. Без индекса или без FTS — возвращает пустой список.

Пример:

```python
results = search_methods("ОбработкаЗаполнения")
for r in results:
    print(f"{r['name']} ({r['type']}) в {r['module_path']}")
```

### Секция INDEX в стратегии

Когда индекс загружен, в стратегии `rlm_start` добавляется секция `== INDEX ==` с информацией:

```
== INDEX ==
Pre-built method index loaded (569068 methods, 4554311 call edges, config: УправлениеПредприятием v2.5.14.59).
extract_procedures() / find_callers_context() / find_event_subscriptions() return instantly from index.
search_methods(query) — full-text search by method name substring.
```

Если индекс устарел, добавляется предупреждение `WARNING`.

### Ответ rlm_start

В JSON-ответ `rlm_start` добавляется поле `index`:

```json
{
  "index": {
    "loaded": true,
    "methods": 569068,
    "calls": 4554311,
    "has_fts": true,
    "config_name": "УправлениеПредприятием",
    "config_version": "2.5.20.80",
    "warnings": []
  }
}
```

## 8. Граф вызовов — ограничения

Граф вызовов строится **эвристически** (regex-разбором), а не на основе AST:

- **Ложные срабатывания:** В граф могут попасть конструкторы (`Новый ТипОбъекта()`), функции платформы, а также совпадения в строковых литералах, не полностью очищенных парсером
- **Квалифицированные вызовы:** Вызовы вида `ОбщийМодуль.Метод()` сохраняются как есть (`callee_name = "ОбщийМодуль.Метод"`), без разрешения в конкретный модуль/файл
- **Отсутствие различения:** Парсер не отличает вызов метода (`Метод()`) от обращения к переменной, оборачивающей вызов
- **Рекомендация:** Для критического анализа (рефакторинг, удаление метода) результаты из графа вызовов следует верифицировать через live-хелперы `read_procedure` и `find_callers_context`, которые работают по актуальным файлам

## 9. Known issues и ограничения

- **UNIQUE(module_id, name, line)** — парсер может создать дубликаты для нестандартного кода (например, несколько процедур с одинаковым именем в одном модуле). При конфликте используется `INSERT OR REPLACE`
- **Переименование методов** — инкрементальное обновление не пересчитывает входящие рёбра в `calls`, если метод был переименован. Для полной корректности графа после массовых переименований рекомендуется `index build`
- **Точность mtime** — зависит от файловой системы (FAT32 — 2 сек, NTFS — 100 нс, ext4 — 1 нс). Допуск в 1 секунду учтён при сравнении
- **Время первого построения** — на ERP (~23K файлов, ~569K методов, ~4.5M вызовов) полный `index build` занимает ~6.5 минут на быстром SSD (calls + metadata + FTS). FTS добавляет ~5 секунд к общему времени. Без графа вызовов (`--no-calls`) — ~30-90 секунд
- **Потребление памяти при построении** — все результаты парсинга (методы + вызовы) накапливаются в ОЗУ до момента записи в SQLite. На конфигурации ERP пиковое потребление составляет ~3 GB RAM. Если на машине мало оперативной памяти, используйте `--no-calls` для снижения нагрузки
- **`extension_purpose`** — ключ в `index_meta` присутствует только для расширений (`config_role = extension`). Для основных конфигураций не записывается
- **Размер БД** — на ERP: ~1118 MB (calls + metadata + FTS), из которых ~232 MB — FTS-индекс (trigram), ~3.8 MB — таблицы метаданных (ES/SJ/FO). Без FTS (`--no-fts`) — ~886 MB. Без графа вызовов (`--no-calls`) — ~35 MB
