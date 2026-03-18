"""Integration tests: BSL helpers accelerated by SQLite index (Stage 2).

Tests verify that when IndexReader is provided to make_bsl_helpers:
- extract_procedures, find_exports, find_callers_context use the index
- find_event_subscriptions, find_scheduled_jobs, find_functional_options use the index
- search_methods works via FTS5
- Without index: helpers fall back to live parsing (existing behavior)
- Strategy text includes INDEX section when index is loaded
"""
import json
import os
import sqlite3

import pytest

from rlm_tools_bsl.bsl_index import (
    IndexBuilder,
    IndexReader,
)
from rlm_tools_bsl.bsl_helpers import make_bsl_helpers
from rlm_tools_bsl.bsl_knowledge import get_strategy
from rlm_tools_bsl.format_detector import detect_format
from rlm_tools_bsl.helpers import make_helpers


# ---------------------------------------------------------------------------
# BSL fixtures
# ---------------------------------------------------------------------------

COMMON_MODULE_BSL = """\
Процедура ЗаполнитьТабличнуюЧасть(ДокументОбъект, ИмяТабличнойЧасти) Экспорт
    Для Каждого Строка Из ДокументОбъект[ИмяТабличнойЧасти] Цикл
        Строка.Количество = 1;
    КонецЦикла;
КонецПроцедуры

Функция ПолучитьДатуСеанса() Экспорт
    Возврат ТекущаяДатаСеанса();
КонецФункции

Процедура ВычислитьИтоги(ТаблицаЗначений)
    Результат = Новый Массив;
КонецПроцедуры
"""

OBJECT_MODULE_BSL = """\
Процедура ОбработкаЗаполнения(ДанныеЗаполнения, СтандартнаяОбработка) Экспорт
    МойМодуль.ЗаполнитьТабличнуюЧасть(ЭтотОбъект, "Товары");
КонецПроцедуры

Процедура ПередЗаписью(Отказ)
    ПроверитьЗаполнение();
КонецПроцедуры
"""

EVENT_SUBSCRIPTION_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
                xmlns:v8="http://v8.1c.ru/8.1/data/core"
                xmlns:xr="http://v8.1c.ru/8.3/xcf/readable">
<EventSubscription>
<Properties>
<Name>ПриЗаписиДокумента</Name>
<Synonym><v8:item><v8:content>При записи документа</v8:content></v8:item></Synonym>
<Source>
<v8:Type>cfg:DocumentObject.ТестовыйДокумент</v8:Type>
</Source>
<Handler>CommonModule.МойМодуль.ПриЗаписиДокументаОбработка</Handler>
<Event>OnWrite</Event>
</Properties>
</EventSubscription>
</MetaDataObject>
"""

SCHEDULED_JOB_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core">
<ScheduledJob>
<Properties>
<Name>ОбновлениеКурсовВалют</Name>
<Synonym><v8:item><v8:content>Обновление курсов валют</v8:content></v8:item></Synonym>
<MethodName>CommonModule.КурсыВалют.ОбновитьКурсы</MethodName>
<Use>true</Use>
<Predefined>true</Predefined>
<RestartCountOnFailure>3</RestartCountOnFailure>
<RestartIntervalOnFailure>60</RestartIntervalOnFailure>
</Properties>
</ScheduledJob>
</MetaDataObject>
"""

FUNCTIONAL_OPTION_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
                xmlns:v8="http://v8.1c.ru/8.1/data/core"
                xmlns:xr="http://v8.1c.ru/8.3/xcf/readable">
<FunctionalOption>
<Properties>
<Name>ИспользоватьВалюту</Name>
<Synonym><v8:item><v8:content>Использовать валюту</v8:content></v8:item></Synonym>
<Location>Constant.ИспользоватьВалюту</Location>
<Content>
<xr:Object>Catalog.Валюты</xr:Object>
<xr:Object>Document.ТестовыйДокумент</xr:Object>
</Content>
</Properties>
</FunctionalOption>
</MetaDataObject>
"""


@pytest.fixture
def tmp_bsl_project(tmp_path):
    """Create a CF-format project with BSL files + metadata XMLs."""
    # CommonModules/МойМодуль/Ext/Module.bsl
    cm_dir = tmp_path / "CommonModules" / "МойМодуль" / "Ext"
    cm_dir.mkdir(parents=True)
    (cm_dir / "Module.bsl").write_text(COMMON_MODULE_BSL, encoding="utf-8-sig")

    # Documents/ТестовыйДокумент/Ext/ObjectModule.bsl
    doc_dir = tmp_path / "Documents" / "ТестовыйДокумент" / "Ext"
    doc_dir.mkdir(parents=True)
    (doc_dir / "ObjectModule.bsl").write_text(OBJECT_MODULE_BSL, encoding="utf-8-sig")

    # EventSubscriptions/ПриЗаписиДокумента/Ext/EventSubscription.xml
    es_dir = tmp_path / "EventSubscriptions" / "ПриЗаписиДокумента" / "Ext"
    es_dir.mkdir(parents=True)
    (es_dir / "EventSubscription.xml").write_text(EVENT_SUBSCRIPTION_XML, encoding="utf-8")

    # ScheduledJobs/ОбновлениеКурсовВалют/Ext/ScheduledJob.xml
    sj_dir = tmp_path / "ScheduledJobs" / "ОбновлениеКурсовВалют" / "Ext"
    sj_dir.mkdir(parents=True)
    (sj_dir / "ScheduledJob.xml").write_text(SCHEDULED_JOB_XML, encoding="utf-8")

    # FunctionalOptions/ИспользоватьВалюту/Ext/FunctionalOption.xml
    fo_dir = tmp_path / "FunctionalOptions" / "ИспользоватьВалюту" / "Ext"
    fo_dir.mkdir(parents=True)
    (fo_dir / "FunctionalOption.xml").write_text(FUNCTIONAL_OPTION_XML, encoding="utf-8")

    # Configuration.xml (minimal, for format detection)
    (tmp_path / "Configuration.xml").write_text("<Configuration/>", encoding="utf-8")

    return tmp_path


@pytest.fixture
def built_index(tmp_bsl_project, monkeypatch):
    """Build a full index (calls + metadata + FTS) and return IndexReader."""
    monkeypatch.setenv("RLM_INDEX_DIR", str(tmp_bsl_project / ".index"))
    builder = IndexBuilder()
    db_path = builder.build(
        str(tmp_bsl_project), build_calls=True, build_metadata=True, build_fts=True,
    )
    reader = IndexReader(db_path)
    yield reader
    reader.close()


def _make_helpers(base_path, idx_reader=None):
    """Build bsl helpers dict with optional IndexReader."""
    helpers, resolve_safe = make_helpers(str(base_path))
    format_info = detect_format(str(base_path))
    bsl = make_bsl_helpers(
        base_path=str(base_path),
        resolve_safe=resolve_safe,
        read_file_fn=helpers["read_file"],
        grep_fn=helpers["grep"],
        glob_files_fn=helpers["glob_files"],
        format_info=format_info,
        idx_reader=idx_reader,
    )
    return bsl


# =====================================================================
# Tests WITH index — fast path
# =====================================================================

class TestWithIndex:

    def test_extract_procedures_from_index(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        procs = bsl["extract_procedures"](
            "CommonModules/МойМодуль/Ext/Module.bsl"
        )
        assert len(procs) == 3
        names = [p["name"] for p in procs]
        assert "ЗаполнитьТабличнуюЧасть" in names
        assert "ПолучитьДатуСеанса" in names
        assert "ВычислитьИтоги" in names

    def test_extract_procedures_export_flag(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        procs = bsl["extract_procedures"](
            "CommonModules/МойМодуль/Ext/Module.bsl"
        )
        by_name = {p["name"]: p for p in procs}
        assert by_name["ЗаполнитьТабличнуюЧасть"]["is_export"] is True
        assert by_name["ВычислитьИтоги"]["is_export"] is False

    def test_find_exports_from_index(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        exports = bsl["find_exports"](
            "CommonModules/МойМодуль/Ext/Module.bsl"
        )
        names = [e["name"] for e in exports]
        assert "ЗаполнитьТабличнуюЧасть" in names
        assert "ПолучитьДатуСеанса" in names
        assert "ВычислитьИтоги" not in names

    def test_find_callers_context_from_index(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        result = bsl["find_callers_context"]("ЗаполнитьТабличнуюЧасть")
        assert "callers" in result
        assert "_meta" in result
        callers = result["callers"]
        assert len(callers) >= 1
        # Should find ObjectModule calling ЗаполнитьТабличнуюЧасть
        caller_files = [c["file"] for c in callers]
        assert any("ObjectModule.bsl" in f for f in caller_files)

    def test_find_event_subscriptions_from_index(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        # All subscriptions
        all_subs = bsl["find_event_subscriptions"]()
        assert len(all_subs) >= 1
        assert all_subs[0]["name"] == "ПриЗаписиДокумента"
        # Filtered by object
        filtered = bsl["find_event_subscriptions"]("ТестовыйДокумент")
        assert len(filtered) >= 1

    def test_find_scheduled_jobs_from_index(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        all_jobs = bsl["find_scheduled_jobs"]()
        assert len(all_jobs) >= 1
        assert all_jobs[0]["name"] == "ОбновлениеКурсовВалют"
        assert all_jobs[0]["use"] is True
        # Filter by name
        filtered = bsl["find_scheduled_jobs"]("Курс")
        assert len(filtered) >= 1

    def test_find_functional_options_from_index(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        result = bsl["find_functional_options"]("ТестовыйДокумент")
        assert "xml_options" in result
        xml_opts = result["xml_options"]
        assert len(xml_opts) >= 1
        assert xml_opts[0]["name"] == "ИспользоватьВалюту"

    def test_search_methods_fts(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        results = bsl["search_methods"]("Заполнить")
        assert len(results) >= 1
        names = [r["name"] for r in results]
        assert "ЗаполнитьТабличнуюЧасть" in names

    def test_search_methods_empty_query(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        results = bsl["search_methods"]("")
        assert results == []

    def test_search_methods_no_match(self, tmp_bsl_project, built_index):
        bsl = _make_helpers(tmp_bsl_project, built_index)
        results = bsl["search_methods"]("НесуществующееИмяМетода12345")
        assert results == []


# =====================================================================
# Tests WITHOUT index — fallback
# =====================================================================

class TestWithoutIndex:

    def test_extract_procedures_fallback(self, tmp_bsl_project):
        bsl = _make_helpers(tmp_bsl_project, idx_reader=None)
        procs = bsl["extract_procedures"](
            "CommonModules/МойМодуль/Ext/Module.bsl"
        )
        assert len(procs) == 3
        names = [p["name"] for p in procs]
        assert "ЗаполнитьТабличнуюЧасть" in names

    def test_find_exports_fallback(self, tmp_bsl_project):
        bsl = _make_helpers(tmp_bsl_project, idx_reader=None)
        exports = bsl["find_exports"](
            "CommonModules/МойМодуль/Ext/Module.bsl"
        )
        assert len(exports) == 2

    def test_find_callers_context_fallback(self, tmp_bsl_project):
        bsl = _make_helpers(tmp_bsl_project, idx_reader=None)
        result = bsl["find_callers_context"]("ЗаполнитьТабличнуюЧасть")
        assert "callers" in result

    def test_find_event_subscriptions_fallback(self, tmp_bsl_project):
        bsl = _make_helpers(tmp_bsl_project, idx_reader=None)
        subs = bsl["find_event_subscriptions"]()
        assert len(subs) >= 1

    def test_find_scheduled_jobs_fallback(self, tmp_bsl_project):
        bsl = _make_helpers(tmp_bsl_project, idx_reader=None)
        jobs = bsl["find_scheduled_jobs"]()
        assert len(jobs) >= 1

    def test_find_functional_options_fallback(self, tmp_bsl_project):
        bsl = _make_helpers(tmp_bsl_project, idx_reader=None)
        result = bsl["find_functional_options"]("ТестовыйДокумент")
        assert "xml_options" in result

    def test_search_methods_without_index(self, tmp_bsl_project):
        bsl = _make_helpers(tmp_bsl_project, idx_reader=None)
        results = bsl["search_methods"]("Заполнить")
        assert results == []  # no index = empty


# =====================================================================
# IndexReader new methods (unit tests)
# =====================================================================

class TestIndexReaderNewMethods:

    def test_get_event_subscriptions_all(self, built_index):
        result = built_index.get_event_subscriptions()
        assert result is not None
        assert len(result) >= 1
        assert result[0]["name"] == "ПриЗаписиДокумента"

    def test_get_event_subscriptions_filtered(self, built_index):
        result = built_index.get_event_subscriptions("ТестовыйДокумент")
        assert result is not None
        assert len(result) >= 1
        # Filtered result includes source_types
        assert "source_types" in result[0]

    def test_get_event_subscriptions_no_match(self, built_index):
        # Our subscription has specific source_types (DocumentObject.ТестовыйДокумент),
        # so filtering by a non-matching object should return empty.
        result = built_index.get_event_subscriptions("НесуществующийОбъект")
        assert result is not None
        assert len(result) == 0

    def test_get_scheduled_jobs_all(self, built_index):
        result = built_index.get_scheduled_jobs()
        assert result is not None
        assert len(result) >= 1
        job = result[0]
        assert job["name"] == "ОбновлениеКурсовВалют"
        assert job["use"] is True
        assert job["predefined"] is True

    def test_get_scheduled_jobs_filtered(self, built_index):
        result = built_index.get_scheduled_jobs("Курс")
        assert result is not None
        assert len(result) >= 1

    def test_get_scheduled_jobs_no_match(self, built_index):
        result = built_index.get_scheduled_jobs("НетТакогоЗадания")
        assert result is not None
        assert len(result) == 0

    def test_get_functional_options_all(self, built_index):
        result = built_index.get_functional_options()
        assert result is not None
        assert len(result) >= 1
        assert result[0]["name"] == "ИспользоватьВалюту"

    def test_get_functional_options_filtered(self, built_index):
        result = built_index.get_functional_options("ТестовыйДокумент")
        assert result is not None
        assert len(result) >= 1

    def test_get_functional_options_no_match(self, built_index):
        result = built_index.get_functional_options("НесуществующийОбъект")
        assert result is not None
        assert len(result) == 0


# =====================================================================
# Strategy text with index
# =====================================================================

class TestStrategyWithIndex:

    def test_strategy_includes_index_section(self):
        idx_stats = {
            "methods": 500,
            "calls": 10000,
            "has_fts": True,
            "config_name": "ТестоваяКонфигурация",
            "config_version": "3.0.1",
        }
        strategy = get_strategy("high", None, idx_stats=idx_stats)
        assert "== INDEX ==" in strategy
        assert "500 methods" in strategy
        assert "10000 call edges" in strategy
        assert "ТестоваяКонфигурация" in strategy
        assert "search_methods" in strategy

    def test_strategy_includes_warnings(self):
        idx_stats = {"methods": 100, "calls": 200, "has_fts": False}
        idx_warnings = ["Index is 10 days old — verify critical findings"]
        strategy = get_strategy(
            "medium", None, idx_stats=idx_stats, idx_warnings=idx_warnings,
        )
        assert "WARNING:" in strategy
        assert "10 days old" in strategy

    def test_strategy_no_fts_no_fts_line_in_index_section(self):
        idx_stats = {"methods": 100, "calls": 200, "has_fts": False}
        strategy = get_strategy("medium", None, idx_stats=idx_stats)
        assert "== INDEX ==" in strategy
        # FTS-specific line should NOT appear in INDEX section
        assert "full-text search by method name" not in strategy

    def test_strategy_without_index(self):
        strategy = get_strategy("medium", None)
        assert "== INDEX ==" not in strategy
