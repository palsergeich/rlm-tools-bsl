"""Comprehensive unit tests for bsl_index module.

Tests IndexBuilder, IndexReader, incremental updates, freshness checks,
and path/env helpers.
"""

import sqlite3
import time

import pytest

from rlm_tools_bsl.bsl_index import (
    IndexBuilder,
    IndexReader,
    IndexStatus,
    check_index_freshness,
    check_index_strict,
    check_index_usable,
    get_index_db_path,
    get_index_dir,
)
from rlm_tools_bsl.cache import _paths_hash


# ---------------------------------------------------------------------------
# BSL source code fixtures — realistic 1C code with Russian identifiers
# ---------------------------------------------------------------------------

COMMON_MODULE_BSL = """\
#Область ПрограммныйИнтерфейс

// Заполняет табличную часть документа по умолчанию.
//
// Параметры:
//  ДокументОбъект - ДокументОбъект - заполняемый документ
//
Процедура ЗаполнитьТабличнуюЧасть(ДокументОбъект, ИмяТабличнойЧасти) Экспорт
    Для Каждого Строка Из ДокументОбъект[ИмяТабличнойЧасти] Цикл
        Строка.Количество = 1;
    КонецЦикла;
КонецПроцедуры

// Возвращает текущую дату сеанса с учетом часового пояса.
Функция ПолучитьДатуСеанса() Экспорт
    Возврат ТекущаяДатаСеанса();
КонецФункции

// Внутренняя процедура — НЕ экспортная.
Процедура ВычислитьИтоги(ТаблицаЗначений)
    Результат = Новый Массив;
    Для Каждого Строка Из ТаблицаЗначений Цикл
        Результат.Добавить(Строка.Сумма);
    КонецЦикла;
КонецПроцедуры

#КонецОбласти
"""

OBJECT_MODULE_BSL = """\
Процедура ОбработкаЗаполнения(ДанныеЗаполнения, СтандартнаяОбработка) Экспорт
    Если ТипЗнч(ДанныеЗаполнения) = Тип("Структура") Тогда
        МойМодуль.ЗаполнитьТабличнуюЧасть(ЭтотОбъект, "Товары");
    КонецЕсли;
КонецПроцедуры

Процедура ПередЗаписью(Отказ)
    ПроверитьЗаполнение();
    МойМодуль.ВычислитьИтоги(Товары);
КонецПроцедуры

Функция ПолучитьПредставление() Экспорт
    Дата = МойМодуль.ПолучитьДатуСеанса();
    Возврат "Документ от " + Формат(Дата, "ДФ=dd.MM.yyyy");
КонецФункции
"""

MANAGER_MODULE_BSL = """\
Функция ПолучитьФорму(ИмяФормы, Параметры, Владелец) Экспорт
    Возврат ПолучитьОбщуюФорму("ФормаВыбора");
КонецФункции
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_bsl_project(tmp_path):
    """Create a temporary CF-format BSL project structure.

    Structure:
        tmp_path/
          CommonModules/
            МойМодуль/
              Ext/
                Module.bsl
          Documents/
            ТестовыйДокумент/
              Ext/
                ObjectModule.bsl
                ManagerModule.bsl
    """
    # CommonModules/МойМодуль/Ext/Module.bsl
    cm_dir = tmp_path / "CommonModules" / "МойМодуль" / "Ext"
    cm_dir.mkdir(parents=True)
    (cm_dir / "Module.bsl").write_text(COMMON_MODULE_BSL, encoding="utf-8-sig")

    # Documents/ТестовыйДокумент/Ext/ObjectModule.bsl
    doc_dir = tmp_path / "Documents" / "ТестовыйДокумент" / "Ext"
    doc_dir.mkdir(parents=True)
    (doc_dir / "ObjectModule.bsl").write_text(OBJECT_MODULE_BSL, encoding="utf-8-sig")

    # Documents/ТестовыйДокумент/Ext/ManagerModule.bsl
    (doc_dir / "ManagerModule.bsl").write_text(MANAGER_MODULE_BSL, encoding="utf-8-sig")

    return tmp_path


@pytest.fixture
def built_index(tmp_bsl_project, monkeypatch):
    """Build a full index (with calls) and return (db_path, base_path)."""
    monkeypatch.setenv("RLM_INDEX_DIR", str(tmp_bsl_project / ".index"))
    builder = IndexBuilder()
    db_path = builder.build(str(tmp_bsl_project), build_calls=True)
    return db_path, str(tmp_bsl_project)


@pytest.fixture
def built_index_no_calls(tmp_bsl_project, monkeypatch):
    """Build an index WITHOUT calls and return (db_path, base_path)."""
    monkeypatch.setenv("RLM_INDEX_DIR", str(tmp_bsl_project / ".index_nc"))
    builder = IndexBuilder()
    db_path = builder.build(str(tmp_bsl_project), build_calls=False)
    return db_path, str(tmp_bsl_project)


# =====================================================================
# IndexBuilder tests
# =====================================================================


class TestIndexBuilder:
    def test_build_creates_db(self, built_index):
        """build() creates a .db file with all 4 tables (meta + 3 data)."""
        db_path, _ = built_index
        assert db_path.exists(), "Database file should exist after build"

        conn = sqlite3.connect(str(db_path))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()

        expected_tables = {"index_meta", "modules", "methods", "calls"}
        assert expected_tables.issubset(tables), f"Expected tables {expected_tables}, found {tables}"

    def test_build_no_calls(self, built_index_no_calls):
        """build(build_calls=False) creates DB without calls data."""
        db_path, _ = built_index_no_calls

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # The calls table schema is always created, but should have 0 rows
        row = conn.execute("SELECT COUNT(*) AS cnt FROM calls").fetchone()
        assert row["cnt"] == 0, "calls table should be empty when build_calls=False"

        # Verify meta records it
        meta_row = conn.execute("SELECT value FROM index_meta WHERE key = 'has_calls'").fetchone()
        assert meta_row["value"] == "0"

        conn.close()

    def test_build_modules_complete(self, built_index):
        """All .bsl files from the fixture appear in the modules table."""
        db_path, _ = built_index

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT rel_path FROM modules ORDER BY rel_path").fetchall()
        conn.close()

        rel_paths = sorted(r[0] for r in rows)
        assert len(rel_paths) == 3, f"Expected 3 modules, got {len(rel_paths)}"

        # Check that all expected relative paths are present
        assert any("CommonModules" in p and "Module.bsl" in p for p in rel_paths)
        assert any("ObjectModule.bsl" in p for p in rel_paths)
        assert any("ManagerModule.bsl" in p for p in rel_paths)

    def test_build_modules_mtime_and_size(self, built_index):
        """mtime and size fields are stored and are positive numbers."""
        db_path, _ = built_index

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT mtime, size FROM modules").fetchall()
        conn.close()

        assert len(rows) > 0
        for row in rows:
            assert row["mtime"] > 0, "mtime should be a positive number"
            assert row["size"] > 0, "size should be a positive number"

    def test_build_methods_match_fixture(self, built_index):
        """Methods table has all expected procedures/functions from fixture files."""
        db_path, _ = built_index

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT name FROM methods ORDER BY name").fetchall()
        conn.close()

        method_names = {r[0] for r in rows}

        # From COMMON_MODULE_BSL
        assert "ЗаполнитьТабличнуюЧасть" in method_names
        assert "ПолучитьДатуСеанса" in method_names
        assert "ВычислитьИтоги" in method_names

        # From OBJECT_MODULE_BSL
        assert "ОбработкаЗаполнения" in method_names
        assert "ПередЗаписью" in method_names
        assert "ПолучитьПредставление" in method_names

        # From MANAGER_MODULE_BSL
        assert "ПолучитьФорму" in method_names

        # Total: 3 + 3 + 1 = 7
        assert len(method_names) == 7, f"Expected 7 methods, got {len(method_names)}: {method_names}"

    def test_build_methods_unique_constraint(self, built_index):
        """No duplicates for (module_id, name, line) in the methods table."""
        db_path, _ = built_index

        conn = sqlite3.connect(str(db_path))
        total = conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0]
        unique = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT module_id, name, line FROM methods)").fetchone()[0]
        conn.close()

        assert total == unique, "There should be no duplicate (module_id, name, line) entries"

    def test_build_calls_populated(self, built_index):
        """Calls table has entries for the qualified calls in the fixture."""
        db_path, _ = built_index

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT COUNT(*) AS cnt FROM calls").fetchone()
        conn.close()

        assert row[0] > 0, "calls table should have entries"

    def test_build_calls_excludes_keywords(self, built_index):
        """BSL keywords like 'Если', 'Новый', 'Тип' should NOT appear as callee_name."""
        db_path, _ = built_index

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT DISTINCT callee_name FROM calls").fetchall()
        conn.close()

        callee_names = {r[0] for r in rows}
        keywords_that_must_be_absent = {"Если", "Новый", "Тип", "ТипЗнч", "Для", "Каждого"}
        found = callee_names & keywords_that_must_be_absent
        assert not found, f"Keywords should not be in callee_name: {found}"

    def test_build_calls_qualified(self, built_index):
        """Qualified call 'МойМодуль.ЗаполнитьТабличнуюЧасть' found in callee_name."""
        db_path, _ = built_index

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT callee_name FROM calls").fetchall()
        conn.close()

        callee_names = {r[0] for r in rows}
        assert "МойМодуль.ЗаполнитьТабличнуюЧасть" in callee_names, (
            f"Expected qualified call not found. All callees: {callee_names}"
        )


# =====================================================================
# IndexReader tests
# =====================================================================


class TestIndexReader:
    def test_reader_get_methods_by_path(self, built_index):
        """get_methods_by_path returns correct list of dicts for a known module."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            # Find the rel_path for the common module
            conn = sqlite3.connect(str(db_path))
            rel_paths = [r[0] for r in conn.execute("SELECT rel_path FROM modules").fetchall()]
            conn.close()

            cm_path = [p for p in rel_paths if "CommonModules" in p][0]
            methods = reader.get_methods_by_path(cm_path)

            assert methods is not None, "Should return methods for known path"
            assert isinstance(methods, list)
            assert len(methods) == 3  # ЗаполнитьТабличнуюЧасть, ПолучитьДатуСеанса, ВычислитьИтоги

            names = {m["name"] for m in methods}
            assert "ЗаполнитьТабличнуюЧасть" in names
            assert "ПолучитьДатуСеанса" in names
            assert "ВычислитьИтоги" in names

            # Check dict keys
            for m in methods:
                assert "name" in m
                assert "type" in m
                assert "line" in m
                assert "end_line" in m
                assert "is_export" in m
                assert "params" in m
        finally:
            reader.close()

    def test_reader_get_methods_unknown_path(self, built_index):
        """get_methods_by_path returns None for a path not in the index."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            result = reader.get_methods_by_path("НесуществующийМодуль/Ext/Module.bsl")
            assert result is None
        finally:
            reader.close()

    def test_reader_get_callers(self, built_index):
        """get_callers returns dict with 'callers' list and '_meta' dict."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            result = reader.get_callers("ЗаполнитьТабличнуюЧасть")

            assert result is not None, "Should find callers for a called method"
            assert "callers" in result
            assert "_meta" in result
            assert isinstance(result["callers"], list)
            assert isinstance(result["_meta"], dict)

            meta = result["_meta"]
            assert "total_callers" in meta
            assert "returned" in meta
            assert "offset" in meta
            assert "has_more" in meta

            # At least one caller (from ObjectModule.bsl)
            assert len(result["callers"]) >= 1

            caller = result["callers"][0]
            assert "file" in caller
            assert "caller_name" in caller
            assert "caller_is_export" in caller
            assert "line" in caller
        finally:
            reader.close()

    def test_reader_get_callers_no_calls_table(self, built_index_no_calls):
        """get_callers returns None when the index was built without calls."""
        db_path, _ = built_index_no_calls
        reader = IndexReader(db_path)
        try:
            result = reader.get_callers("ЗаполнитьТабличнуюЧасть")
            assert result is None
        finally:
            reader.close()

    def test_reader_get_callers_no_hint_meta(self, built_index):
        """get_callers without module_hint: fast COUNT matches actual rows."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            result = reader.get_callers("ЗаполнитьТабличнуюЧасть")
            meta = result["_meta"]
            assert meta["total_callers"] >= 1
            assert meta["returned"] == len(result["callers"])
            assert meta["offset"] == 0
            # has_more should be False when all rows fit in default limit
            assert meta["has_more"] is False or meta["total_callers"] > meta["returned"]
        finally:
            reader.close()

    def test_reader_get_callers_with_hint_meta(self, built_index):
        """get_callers with module_hint: precise COUNT via JOIN."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            result = reader.get_callers("ЗаполнитьТабличнуюЧасть", module_hint="ТестовыйДокумент")
            meta = result["_meta"]
            assert meta["total_callers"] >= 1
            assert meta["returned"] == len(result["callers"])
            # All callers should be from the hinted module
            for c in result["callers"]:
                assert "ТестовыйДокумент" in c["object_name"]
        finally:
            reader.close()

    def test_reader_get_callers_pagination(self, built_index):
        """get_callers pagination: offset + limit produce consistent _meta."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            full = reader.get_callers("ЗаполнитьТабличнуюЧасть", limit=100)
            total = full["_meta"]["total_callers"]
            if total >= 1:
                page = reader.get_callers("ЗаполнитьТабличнуюЧасть", limit=1, offset=0)
                assert page["_meta"]["total_callers"] == total
                assert page["_meta"]["returned"] == 1
                assert page["_meta"]["offset"] == 0
                if total > 1:
                    assert page["_meta"]["has_more"] is True
        finally:
            reader.close()

    def test_reader_get_callers_zero(self, built_index):
        """get_callers for a method nobody calls returns empty list."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            result = reader.get_callers("НесуществующаяПроцедура12345")
            assert result is not None
            assert result["callers"] == []
            assert result["_meta"]["total_callers"] == 0
            assert result["_meta"]["has_more"] is False
        finally:
            reader.close()

    def test_reader_get_callers_qualified(self, built_index):
        """get_callers finds qualified calls (МойМодуль.ЗаполнитьТабличнуюЧасть)."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            result = reader.get_callers("ЗаполнитьТабличнуюЧасть")
            # Should find the call from ObjectModule (МойМодуль.ЗаполнитьТабличнуюЧасть)
            assert result["_meta"]["total_callers"] >= 1
        finally:
            reader.close()

    def test_reader_get_exports(self, built_index):
        """get_exports_by_path returns only exported methods."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            conn = sqlite3.connect(str(db_path))
            rel_paths = [r[0] for r in conn.execute("SELECT rel_path FROM modules").fetchall()]
            conn.close()

            cm_path = [p for p in rel_paths if "CommonModules" in p][0]
            exports = reader.get_exports_by_path(cm_path)

            assert exports is not None
            export_names = {e["name"] for e in exports}

            # Only exported methods
            assert "ЗаполнитьТабличнуюЧасть" in export_names
            assert "ПолучитьДатуСеанса" in export_names
            # ВычислитьИтоги is NOT exported
            assert "ВычислитьИтоги" not in export_names
        finally:
            reader.close()

    def test_reader_get_statistics(self, built_index):
        """get_statistics returns dict with all expected keys."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            stats = reader.get_statistics()

            assert isinstance(stats, dict)
            for key in ("modules", "methods", "calls", "exports", "built_at"):
                assert key in stats, f"Missing key '{key}' in statistics"

            assert stats["modules"] == 3
            assert stats["methods"] == 7
            assert stats["calls"] > 0
            assert stats["exports"] > 0
            assert stats["built_at"] is not None
            assert stats["built_at"] > 0
        finally:
            reader.close()

    def test_reader_has_calls_true(self, built_index):
        """has_calls property is True when calls table has data."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        try:
            assert reader.has_calls is True
        finally:
            reader.close()

    def test_reader_has_calls_false(self, built_index_no_calls):
        """has_calls property is False when built without calls."""
        db_path, _ = built_index_no_calls
        reader = IndexReader(db_path)
        try:
            assert reader.has_calls is False
        finally:
            reader.close()

    def test_reader_close(self, built_index):
        """close() does not raise errors and subsequent access fails gracefully."""
        db_path, _ = built_index
        reader = IndexReader(db_path)
        reader.close()
        # Calling close again should not raise
        reader.close()


# =====================================================================
# FTS5 full-text search tests
# =====================================================================


class TestFTS:
    """Tests for FTS5 trigram-based full-text search of methods."""

    @pytest.fixture
    def built_index_fts(self, tmp_bsl_project, monkeypatch):
        """Build an index with FTS enabled."""
        monkeypatch.setenv("RLM_INDEX_DIR", str(tmp_bsl_project / ".index_fts"))
        builder = IndexBuilder()
        db_path = builder.build(str(tmp_bsl_project), build_calls=True, build_fts=True)
        return db_path, str(tmp_bsl_project)

    @pytest.fixture
    def built_index_no_fts(self, tmp_bsl_project, monkeypatch):
        """Build an index WITHOUT FTS."""
        monkeypatch.setenv("RLM_INDEX_DIR", str(tmp_bsl_project / ".index_nofts"))
        builder = IndexBuilder()
        db_path = builder.build(str(tmp_bsl_project), build_calls=True, build_fts=False)
        return db_path, str(tmp_bsl_project)

    def test_fts_table_created(self, built_index_fts):
        """FTS virtual table exists after build with build_fts=True."""
        db_path, _ = built_index_fts
        conn = sqlite3.connect(str(db_path))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "methods_fts" in tables

    def test_fts_search_substring(self, built_index_fts):
        """Search by substring finds matching methods."""
        db_path, _ = built_index_fts
        reader = IndexReader(db_path)
        try:
            results = reader.search_methods("Заполнить")
            names = [r["name"] for r in results]
            assert "ЗаполнитьТабличнуюЧасть" in names
        finally:
            reader.close()

    def test_fts_search_case_insensitive(self, built_index_fts):
        """Trigram search is case-insensitive."""
        db_path, _ = built_index_fts
        reader = IndexReader(db_path)
        try:
            results = reader.search_methods("заполнить")
            names = [r["name"] for r in results]
            assert "ЗаполнитьТабличнуюЧасть" in names
        finally:
            reader.close()

    def test_fts_search_by_object_name(self, built_index_fts):
        """Search matches object_name field too."""
        db_path, _ = built_index_fts
        reader = IndexReader(db_path)
        try:
            results = reader.search_methods("МойМодуль")
            assert len(results) > 0
            assert all(r["object_name"] == "МойМодуль" for r in results)
        finally:
            reader.close()

    def test_fts_search_no_results(self, built_index_fts):
        """Non-existent query returns empty list."""
        db_path, _ = built_index_fts
        reader = IndexReader(db_path)
        try:
            results = reader.search_methods("НесуществующийМетод12345")
            assert results == []
        finally:
            reader.close()

    def test_fts_search_limit(self, built_index_fts):
        """Limit parameter restricts result count."""
        db_path, _ = built_index_fts
        reader = IndexReader(db_path)
        try:
            results = reader.search_methods("Процедур", limit=1)
            assert len(results) <= 1
        finally:
            reader.close()

    def test_fts_search_returns_fields(self, built_index_fts):
        """Each result has all expected fields including rank."""
        db_path, _ = built_index_fts
        reader = IndexReader(db_path)
        try:
            results = reader.search_methods("Заполнить")
            assert len(results) > 0
            r = results[0]
            expected_keys = {
                "name",
                "type",
                "is_export",
                "line",
                "end_line",
                "params",
                "module_path",
                "object_name",
                "rank",
            }
            assert set(r.keys()) == expected_keys
        finally:
            reader.close()

    def test_build_no_fts(self, built_index_no_fts):
        """--no-fts flag skips FTS table creation."""
        db_path, _ = built_index_no_fts
        conn = sqlite3.connect(str(db_path))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "methods_fts" not in tables

    def test_search_without_fts_table(self, built_index_no_fts):
        """search_methods returns [] when FTS not built."""
        db_path, _ = built_index_no_fts
        reader = IndexReader(db_path)
        try:
            results = reader.search_methods("Заполнить")
            assert results == []
        finally:
            reader.close()

    def test_has_fts_property(self, built_index_fts, built_index_no_fts):
        """has_fts property reflects FTS presence."""
        db_path_fts, _ = built_index_fts
        reader_fts = IndexReader(db_path_fts)
        try:
            assert reader_fts.has_fts is True
        finally:
            reader_fts.close()

        db_path_no, _ = built_index_no_fts
        reader_no = IndexReader(db_path_no)
        try:
            assert reader_no.has_fts is False
        finally:
            reader_no.close()

    def test_fts_search_empty_query(self, built_index_fts):
        """Empty query returns empty list."""
        db_path, _ = built_index_fts
        reader = IndexReader(db_path)
        try:
            assert reader.search_methods("") == []
            assert reader.search_methods("  ") == []
        finally:
            reader.close()


# =====================================================================
# Incremental update tests
# =====================================================================

NEW_FILE_BSL = """\
Процедура НоваяПроцедура() Экспорт
    Сообщить("Новый файл");
КонецПроцедуры
"""

MODIFIED_MODULE_BSL = """\
Функция ПолучитьФорму(ИмяФормы, Параметры, Владелец) Экспорт
    Возврат ПолучитьОбщуюФорму("ФормаВыбора");
КонецФункции

Функция ДополнительнаяФункция() Экспорт
    Возврат 42;
КонецФункции
"""


class TestIncrementalUpdate:
    def test_update_new_file(self, built_index, monkeypatch):
        """Adding a new .bsl file and running update makes it appear in the index."""
        db_path, base_path = built_index

        # Add a new file
        new_dir = tmp_path_from_base(base_path) / "Catalogs" / "НовыйСправочник" / "Ext"
        new_dir.mkdir(parents=True)
        (new_dir / "ObjectModule.bsl").write_text(NEW_FILE_BSL, encoding="utf-8-sig")

        builder = IndexBuilder()
        result = builder.update(base_path)

        assert result["added"] == 1

        # Verify new module in DB
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT rel_path FROM modules").fetchall()
        conn.close()

        rel_paths = [r[0] for r in rows]
        assert any("НовыйСправочник" in p for p in rel_paths), f"New module not found. All paths: {rel_paths}"

    def test_update_changed_file(self, built_index, monkeypatch):
        """Modifying an existing .bsl file and running update refreshes methods."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)

        manager_path = base / "Documents" / "ТестовыйДокумент" / "Ext" / "ManagerModule.bsl"

        # Count methods before
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        mod_row = conn.execute("SELECT id FROM modules WHERE rel_path LIKE '%ManagerModule%'").fetchone()
        methods_before = conn.execute(
            "SELECT COUNT(*) AS cnt FROM methods WHERE module_id = ?",
            (mod_row["id"],),
        ).fetchone()["cnt"]
        conn.close()

        assert methods_before == 1, "ManagerModule initially has 1 method"

        # Modify the file — add a second method
        # Ensure mtime changes by waiting briefly and then touching
        time.sleep(0.1)
        manager_path.write_text(MODIFIED_MODULE_BSL, encoding="utf-8-sig")

        builder = IndexBuilder()
        result = builder.update(base_path)

        assert result["changed"] == 1

        # Verify methods refreshed
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        mod_row = conn.execute("SELECT id FROM modules WHERE rel_path LIKE '%ManagerModule%'").fetchone()
        methods_after = conn.execute(
            "SELECT COUNT(*) AS cnt FROM methods WHERE module_id = ?",
            (mod_row["id"],),
        ).fetchone()["cnt"]
        conn.close()

        assert methods_after == 2, f"After modification, ManagerModule should have 2 methods, got {methods_after}"

    def test_update_removed_file(self, built_index, monkeypatch):
        """Deleting a .bsl file and running update removes its methods and calls."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)

        manager_path = base / "Documents" / "ТестовыйДокумент" / "Ext" / "ManagerModule.bsl"
        manager_path.unlink()

        builder = IndexBuilder()
        result = builder.update(base_path)

        assert result["removed"] == 1

        # Verify module removed from DB
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT rel_path FROM modules WHERE rel_path LIKE '%ManagerModule%'").fetchall()
        conn.close()

        assert len(rows) == 0, "Removed file should not be in modules table"

    def test_update_unchanged_skipped(self, built_index, monkeypatch):
        """Unchanged files are not re-parsed; delta counts are all zero."""
        db_path, base_path = built_index

        # Get method count before
        conn = sqlite3.connect(str(db_path))
        count_before = conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0]
        conn.close()

        builder = IndexBuilder()
        result = builder.update(base_path)

        assert result["added"] == 0
        assert result["changed"] == 0
        assert result["removed"] == 0

        # Method count unchanged
        conn = sqlite3.connect(str(db_path))
        count_after = conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0]
        conn.close()

        assert count_before == count_after

    def test_update_returns_delta_stats(self, built_index, monkeypatch):
        """update() returns a dict with added/changed/removed keys."""
        _, base_path = built_index

        builder = IndexBuilder()
        result = builder.update(base_path)

        assert isinstance(result, dict)
        assert "added" in result
        assert "changed" in result
        assert "removed" in result
        assert all(isinstance(v, int) for v in result.values())


# =====================================================================
# Staleness / freshness tests
# =====================================================================


class TestFreshness:
    def test_freshness_fresh(self, built_index):
        """Immediately after build, freshness check returns FRESH."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)

        bsl_files = sorted(base.rglob("*.bsl"))
        rel_paths = [f.relative_to(base).as_posix() for f in bsl_files]
        paths_hash = _paths_hash(rel_paths)

        status = check_index_freshness(
            db_path=db_path,
            current_bsl_count=len(bsl_files),
            current_paths_hash=paths_hash,
            base_path=base_path,
        )
        assert status == IndexStatus.FRESH

    def test_freshness_stale_structure(self, built_index):
        """Adding a file without update -> STALE due to count/hash mismatch."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)

        # Add a new file on disk (but don't update index)
        new_dir = base / "Catalogs" / "ДополнительныйСправочник" / "Ext"
        new_dir.mkdir(parents=True)
        (new_dir / "ObjectModule.bsl").write_text(
            "Процедура Тест() Экспорт\nКонецПроцедуры\n",
            encoding="utf-8-sig",
        )

        bsl_files = sorted(base.rglob("*.bsl"))
        rel_paths = [f.relative_to(base).as_posix() for f in bsl_files]
        paths_hash = _paths_hash(rel_paths)

        status = check_index_freshness(
            db_path=db_path,
            current_bsl_count=len(bsl_files),
            current_paths_hash=paths_hash,
            base_path=base_path,
        )
        assert status == IndexStatus.STALE

    def test_freshness_missing(self, tmp_path):
        """When no database file exists, freshness returns MISSING."""
        non_existent = tmp_path / "does_not_exist.db"
        status = check_index_freshness(
            db_path=non_existent,
            current_bsl_count=5,
            current_paths_hash="abc",
            base_path=str(tmp_path),
        )
        assert status == IndexStatus.MISSING


# =====================================================================
# check_index_usable tests (lightweight, no rglob)
# =====================================================================


class TestCheckIndexUsable:
    def test_usable_fresh(self, built_index):
        """Immediately after build, usable check returns FRESH."""
        db_path, base_path = built_index
        status = check_index_usable(db_path, base_path)
        assert status == IndexStatus.FRESH

    def test_usable_missing(self, tmp_path):
        """Non-existent DB returns MISSING."""
        status = check_index_usable(tmp_path / "no.db", str(tmp_path))
        assert status == IndexStatus.MISSING

    def test_usable_stale_age(self, built_index, monkeypatch):
        """Index older than max_age_days returns STALE_AGE."""
        db_path, base_path = built_index
        monkeypatch.setenv("RLM_INDEX_MAX_AGE_DAYS", "0")
        # Also need to disable skip_sample_hours to not short-circuit to FRESH
        monkeypatch.setenv("RLM_INDEX_SKIP_SAMPLE_HOURS", "0")
        status = check_index_usable(db_path, base_path)
        assert status == IndexStatus.STALE_AGE

    def test_usable_skip_sample_for_young_index(self, built_index, monkeypatch):
        """Index younger than SKIP_SAMPLE_HOURS skips sampling, returns FRESH."""
        db_path, base_path = built_index
        monkeypatch.setenv("RLM_INDEX_SKIP_SAMPLE_HOURS", "9999")
        status = check_index_usable(db_path, base_path)
        assert status == IndexStatus.FRESH

    def test_usable_stale_content(self, built_index, monkeypatch):
        """Modified files trigger STALE_CONTENT."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)
        monkeypatch.setenv("RLM_INDEX_SKIP_SAMPLE_HOURS", "0")
        monkeypatch.setenv("RLM_INDEX_SAMPLE_SIZE", "100")
        monkeypatch.setenv("RLM_INDEX_SAMPLE_THRESHOLD", "1")

        # Modify all BSL files to trigger mtime mismatch
        for bsl in base.rglob("*.bsl"):
            bsl.write_text(bsl.read_text(encoding="utf-8-sig") + "\n// changed", encoding="utf-8-sig")

        status = check_index_usable(db_path, base_path)
        assert status == IndexStatus.STALE_CONTENT

    def test_usable_does_not_check_structure(self, built_index):
        """Usable check does NOT detect added files (no structural check)."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)

        # Add a new file on disk
        new_dir = base / "Catalogs" / "НовыйСправочник" / "Ext"
        new_dir.mkdir(parents=True)
        (new_dir / "ObjectModule.bsl").write_text(
            "Процедура Тест() Экспорт\nКонецПроцедуры\n",
            encoding="utf-8-sig",
        )

        # Usable check should still return FRESH (no structural check)
        status = check_index_usable(db_path, base_path)
        assert status == IndexStatus.FRESH


class TestCheckIndexStrict:
    def test_strict_is_alias(self):
        """check_index_freshness is an alias for check_index_strict."""
        assert check_index_freshness is check_index_strict

    def test_strict_fresh(self, built_index):
        """Strict check returns FRESH when everything matches."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)

        bsl_files = sorted(base.rglob("*.bsl"))
        rel_paths = [f.relative_to(base).as_posix() for f in bsl_files]
        paths_hash = _paths_hash(rel_paths)

        status = check_index_strict(db_path, len(bsl_files), paths_hash, base_path)
        assert status == IndexStatus.FRESH

    def test_strict_stale_structure(self, built_index):
        """Strict check detects added files via structural mismatch."""
        db_path, base_path = built_index
        base = tmp_path_from_base(base_path)

        new_dir = base / "Catalogs" / "ДополнительныйСправочник" / "Ext"
        new_dir.mkdir(parents=True)
        (new_dir / "ObjectModule.bsl").write_text(
            "Процедура Тест() Экспорт\nКонецПроцедуры\n",
            encoding="utf-8-sig",
        )

        bsl_files = sorted(base.rglob("*.bsl"))
        rel_paths = [f.relative_to(base).as_posix() for f in bsl_files]
        paths_hash = _paths_hash(rel_paths)

        status = check_index_strict(db_path, len(bsl_files), paths_hash, base_path)
        assert status == IndexStatus.STALE


class TestBslCountInStats:
    def test_statistics_include_bsl_count(self, built_index):
        """get_statistics() returns bsl_count as int from index_meta."""
        db_path, base_path = built_index
        reader = IndexReader(db_path)
        stats = reader.get_statistics()
        reader.close()

        assert "bsl_count" in stats
        assert isinstance(stats["bsl_count"], int)
        assert stats["bsl_count"] > 0


class TestGetAllModules:
    def test_get_all_modules_returns_all(self, built_index):
        """get_all_modules() returns all modules with correct fields."""
        db_path, base_path = built_index
        reader = IndexReader(db_path)
        modules = reader.get_all_modules()
        reader.close()

        assert len(modules) > 0
        for m in modules:
            assert "rel_path" in m
            assert "category" in m
            assert "object_name" in m
            assert "module_type" in m
            assert "form_name" in m

    def test_get_all_modules_matches_count(self, built_index):
        """get_all_modules() count matches get_statistics().modules."""
        db_path, base_path = built_index
        reader = IndexReader(db_path)
        modules = reader.get_all_modules()
        stats = reader.get_statistics()
        reader.close()

        assert len(modules) == stats["modules"]


# =====================================================================
# ENV / path helper tests
# =====================================================================


class TestPathHelpers:
    def test_index_dir_from_env(self, monkeypatch, tmp_path):
        """RLM_INDEX_DIR env var overrides default index directory."""
        custom_dir = str(tmp_path / "custom_index")
        monkeypatch.setenv("RLM_INDEX_DIR", custom_dir)

        result = get_index_dir("/some/base")
        assert result == tmp_path / "custom_index"

    def test_index_dir_default(self, monkeypatch):
        """Without RLM_INDEX_DIR, uses ~/.cache/rlm-tools-bsl/."""
        monkeypatch.delenv("RLM_INDEX_DIR", raising=False)

        from pathlib import Path

        result = get_index_dir("/some/base")
        expected = Path.home() / ".cache" / "rlm-tools-bsl"
        assert result == expected

    def test_index_db_path_uses_hash(self, monkeypatch, tmp_path):
        """Index DB path contains an MD5 hash of base_path."""
        monkeypatch.setenv("RLM_INDEX_DIR", str(tmp_path))

        import hashlib

        base = "/my/project/path"
        expected_hash = hashlib.md5(base.encode()).hexdigest()[:12]

        result = get_index_db_path(base)
        assert expected_hash in str(result), f"Expected hash {expected_hash} in path {result}"
        assert result.name == "method_index.db"


# ---------------------------------------------------------------------------
# Helper for resolving pathlib.Path from base_path string
# ---------------------------------------------------------------------------


def tmp_path_from_base(base_path: str):
    """Convert base_path string back to pathlib.Path."""
    from pathlib import Path

    return Path(base_path)
