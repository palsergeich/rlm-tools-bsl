# Руководство разработчика (внутренние чеклисты)

## Добавление новой таблицы индекса (17 шагов)

1. **`bsl_index.py` `_SCHEMA_SQL`** — `CREATE TABLE` с колонками и типами
2. **`bsl_index.py` `_SCHEMA_SQL`** — `CREATE INDEX` для поиска (NOCASE для текстовых)
3. **`bsl_index.py` `_collect_metadata_tables()`** — сбор данных из XML/файлов
4. **`bsl_xml_parsers.py`** — парсер для CF-формата (если XML-источник)
5. **`bsl_xml_parsers.py`** — парсер для EDT-формата (если `.mdo`-источник)
6. **`bsl_index.py` `_insert_metadata_tables()`** — `INSERT` собранных данных
7. **`bsl_index.py` `IndexBuilder.build()`** — интеграция в полную сборку
8. **`bsl_index.py` `IndexBuilder.update()`** — `CREATE TABLE IF NOT EXISTS` для совместимости со старыми индексами
9. **`bsl_index.py` `index_meta`** — ключи `has_X`, `X_count` в метаданных
10. **`bsl_index.py` `IndexReader`** — `get_X()` метод с параметром `limit`
11. **`bsl_index.py` `get_statistics()`** — счётчик в статистике
12. **`cli.py`** — вывод в `build` / `info`
13. **`bsl_helpers.py`** — helper-функция + `_reg(name, fn, sig, cat, kw, recipe)`
14. **`bsl_knowledge.py`** — WORKFLOW, INDEX TIPS, recipe (если бизнес-домен)
15. **`docs/INDEXING.md`** — схема таблицы, API, benchmark
16. **`docs/HELPERS.md`** — описание хелпера
17. **`tests/`** — тесты: builder, reader, helper, CLI assertions
18. **Git fast path** — обновить `_update_git_fast()`, `_collect_metadata_tables()` kwargs, `_insert_metadata_tables_selective()` — см. [INDEXING.md § 6.1 «Добавление новых категорий / таблиц»](INDEXING.md#добавление-новых-категорий--таблиц-чеклист-для-разработчика)

## Добавление нового хелпера (4 шага)

1. **`bsl_helpers.py`** — функция + `_reg(name, fn, sig, cat, kw, recipe)`
2. **`bsl_knowledge.py`** — recipe (если хелпер привязан к бизнес-домену)
3. **`docs/HELPERS.md`** — описание в соответствующей категории
4. **`tests/`** — тесты (вызов, параметры, ожидаемый вывод)

## Добавление нового бизнес-рецепта (2 шага)

1. **`bsl_knowledge.py` `_BUSINESS_RECIPES`** — новый рецепт (ключевые слова + шаблон ответа)
2. **`tests/`** — тест на matching (ключевые слова совпадают, рецепт возвращается)
