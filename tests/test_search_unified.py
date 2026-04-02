"""Tests for v1.5.1 Unified search() helper.

Tests: fan-out diversity, per-source quota, scope filtering, validation,
empty query, edge cases, result structure, graceful degradation, registration, strategy.
"""

import pytest

from rlm_tools_bsl.bsl_index import IndexBuilder, IndexReader
from rlm_tools_bsl.bsl_helpers import make_bsl_helpers


# ---------------------------------------------------------------------------
# Fixture: full index with methods (FTS), synonyms, regions, headers
# ---------------------------------------------------------------------------

_CF_DOCUMENT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
                xmlns:mdclass="http://v8.1c.ru/8.3/MDClasses"
                xmlns:v8="http://v8.1c.ru/8.1/data/core">
<Document>
  <Properties>
    <Name>ЗаполнениеДокумент</Name>
    <Synonym>
      <v8:item>
        <v8:lang>ru</v8:lang>
        <v8:content>Заполнение документ</v8:content>
      </v8:item>
    </Synonym>
  </Properties>
</Document>
</MetaDataObject>
"""

_CF_REGISTER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
                xmlns:mdclass="http://v8.1c.ru/8.3/MDClasses"
                xmlns:v8="http://v8.1c.ru/8.1/data/core">
<InformationRegister>
  <Properties>
    <Name>СебестоимостьТоваров</Name>
    <Synonym>
      <v8:item>
        <v8:lang>ru</v8:lang>
        <v8:content>Себестоимость товаров</v8:content>
      </v8:item>
    </Synonym>
  </Properties>
</InformationRegister>
</MetaDataObject>
"""

_BSL_MODULE = """\
// Модуль заполнения табличных частей
// Доработка: заполнение себестоимости

#Область ЗаполнениеТабличныхЧастей

Процедура ЗаполнитьТабличнуюЧасть(Параметр1) Экспорт
    // тело
КонецПроцедуры

Функция ПолучитьДанныеЗаполнения() Экспорт
    Возврат Неопределено;
КонецФункции

#КонецОбласти

#Область СлужебныеПроцедуры

Процедура Вспомогательная()
    // внутренняя
КонецПроцедуры

#КонецОбласти
"""

_BSL_DOC_MODULE = """\
#Область ОбработчикиСобытий

Процедура ПриЗаписи(Отказ)
КонецПроцедуры

#КонецОбласти
"""


def _create_full_fixture(tmp_path):
    """Create CF-format project with BSL, XML, regions, headers — all sources."""
    # CommonModules/ЗаполнениеМодуль/Ext/Module.bsl
    cm_dir = tmp_path / "CommonModules" / "ЗаполнениеМодуль" / "Ext"
    cm_dir.mkdir(parents=True)
    (cm_dir / "Module.bsl").write_text(_BSL_MODULE, encoding="utf-8-sig")

    # CommonModules/ЗаполнениеМодуль.xml (synonym for common module)
    cm_xml_dir = tmp_path / "CommonModules"
    (cm_xml_dir / "ЗаполнениеМодуль.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"\n'
        '                xmlns:mdclass="http://v8.1c.ru/8.3/MDClasses"\n'
        '                xmlns:v8="http://v8.1c.ru/8.1/data/core">\n'
        "<CommonModule>\n"
        "  <Properties>\n"
        "    <Name>ЗаполнениеМодуль</Name>\n"
        "    <Synonym>\n"
        "      <v8:item>\n"
        "        <v8:lang>ru</v8:lang>\n"
        "        <v8:content>Заполнение модуль</v8:content>\n"
        "      </v8:item>\n"
        "    </Synonym>\n"
        "  </Properties>\n"
        "</CommonModule>\n"
        "</MetaDataObject>\n",
        encoding="utf-8",
    )

    # Documents/ЗаполнениеДокумент/Ext/ObjectModule.bsl
    doc_dir = tmp_path / "Documents" / "ЗаполнениеДокумент" / "Ext"
    doc_dir.mkdir(parents=True)
    (doc_dir / "ObjectModule.bsl").write_text(_BSL_DOC_MODULE, encoding="utf-8-sig")

    # Documents/ЗаполнениеДокумент.xml
    doc_xml_dir = tmp_path / "Documents"
    (doc_xml_dir / "ЗаполнениеДокумент.xml").write_text(_CF_DOCUMENT_XML, encoding="utf-8")

    # InformationRegisters/СебестоимостьТоваров.xml
    reg_dir = tmp_path / "InformationRegisters"
    reg_dir.mkdir(parents=True)
    (reg_dir / "СебестоимостьТоваров.xml").write_text(_CF_REGISTER_XML, encoding="utf-8")

    # Configuration.xml
    (tmp_path / "Configuration.xml").write_text("<Configuration/>")

    return tmp_path


@pytest.fixture
def full_index(tmp_path, monkeypatch):
    """Build full index with FTS + synonyms + regions + headers."""
    project = _create_full_fixture(tmp_path)
    monkeypatch.setenv("RLM_INDEX_DIR", str(project / ".index"))
    builder = IndexBuilder()
    db_path = builder.build(
        str(project),
        build_calls=False,
        build_fts=True,
        build_synonyms=True,
    )
    reader = IndexReader(str(db_path))
    bsl = make_bsl_helpers(
        base_path=str(project),
        resolve_safe=lambda p: __import__("pathlib").Path(p),
        read_file_fn=lambda p: "",
        grep_fn=lambda pat, path="": [],
        glob_files_fn=lambda pat: [],
        idx_reader=reader,
    )
    yield bsl, reader
    reader.close()


def _make_bsl_no_index():
    """BSL helpers without any index."""
    return make_bsl_helpers(
        base_path="/nonexistent",
        resolve_safe=lambda p: __import__("pathlib").Path(p),
        read_file_fn=lambda p: "",
        grep_fn=lambda pat, path="": [],
        glob_files_fn=lambda pat: [],
    )


# ---------------------------------------------------------------------------
# Fan-out and diversity
# ---------------------------------------------------------------------------


class TestSearchDiversity:
    def test_search_all_sources_diversity(self, full_index):
        """search('Заполнение') should return at least 2 distinct source_types."""
        bsl, _ = full_index
        results = bsl["search"]("Заполнение", limit=30)
        source_types = {r["source_type"] for r in results}
        assert len(source_types) >= 2, f"Expected >=2 source types, got {source_types}"

    def test_search_all_per_source_quota(self, full_index):
        """No single source_type should exceed per-source quota."""
        bsl, _ = full_index
        results = bsl["search"]("Заполнение", limit=30)
        per_source = max(30 // 6, 3)
        from collections import Counter

        counts = Counter(r["source_type"] for r in results)
        for src, cnt in counts.items():
            assert cnt <= per_source, f"{src} has {cnt} results, quota is {per_source}"


# ---------------------------------------------------------------------------
# Scope filtering
# ---------------------------------------------------------------------------


class TestSearchScope:
    def test_search_scope_methods(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("Заполнить", scope="methods")
        assert len(results) >= 1
        assert all(r["source_type"] == "method" for r in results)

    def test_search_scope_objects(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("Заполнение", scope="objects")
        assert len(results) >= 1
        assert all(r["source_type"] == "object" for r in results)

    def test_search_scope_regions(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("Заполнение", scope="regions")
        assert len(results) >= 1
        assert all(r["source_type"] == "region" for r in results)

    def test_search_scope_headers(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("заполнения", scope="headers")
        assert len(results) >= 1
        assert all(r["source_type"] == "header" for r in results)


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------


class TestSearchValidation:
    def test_search_invalid_scope(self, full_index):
        bsl, _ = full_index
        with pytest.raises(ValueError, match="Unknown scope"):
            bsl["search"]("test", scope="invalid")


# ---------------------------------------------------------------------------
# Empty query
# ---------------------------------------------------------------------------


class TestSearchEmptyQuery:
    def test_search_empty_query_all(self, full_index):
        bsl, _ = full_index
        assert bsl["search"]("") == []

    def test_search_empty_query_specific_scope(self, full_index):
        """Empty query + specific scope delegates to underlying helper (browse mode)."""
        bsl, _ = full_index
        results = bsl["search"]("", scope="objects")
        assert len(results) >= 1

    def test_search_whitespace_query(self, full_index):
        bsl, _ = full_index
        assert bsl["search"]("   ") == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestSearchEdgeCases:
    def test_search_no_index(self):
        bsl = _make_bsl_no_index()
        assert bsl["search"]("test") == []

    def test_search_limit(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("Заполнение", limit=2)
        assert len(results) <= 2

    def test_search_strips_query(self, full_index):
        """Leading/trailing whitespace must not break FTS fan-out."""
        bsl, _ = full_index
        clean = bsl["search"]("Заполнить", scope="methods")
        padded = bsl["search"](" Заполнить ", scope="methods")
        assert len(padded) == len(clean)
        assert len(clean) >= 1


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


class TestSearchResultStructure:
    _REQUIRED_KEYS = {"text", "source_type", "object_name", "path", "path_kind", "detail"}

    def test_search_result_structure(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("Заполнение", limit=30)
        assert len(results) >= 1
        for r in results:
            assert self._REQUIRED_KEYS <= set(r.keys()), f"Missing keys in {r}"

    def test_search_path_kind_bsl(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("Заполнить", scope="methods")
        for r in results:
            assert r["path_kind"] == "bsl"

    def test_search_path_kind_metadata(self, full_index):
        bsl, _ = full_index
        results = bsl["search"]("Заполнение", scope="objects")
        for r in results:
            assert r["path_kind"] == "metadata"

    def test_search_detail_preserves_original(self, full_index):
        bsl, _ = full_index
        # Methods have 'rank' and 'line'
        methods = bsl["search"]("Заполнить", scope="methods")
        if methods:
            assert "rank" in methods[0]["detail"] or "line" in methods[0]["detail"]

        # Regions have 'line'
        regions = bsl["search"]("Заполнение", scope="regions")
        if regions:
            assert "line" in regions[0]["detail"]


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestSearchGracefulDegradation:
    def test_search_no_synonyms_table(self, tmp_path, monkeypatch):
        """Index without object_synonyms table — search() still works for other sources."""
        project = _create_full_fixture(tmp_path)
        monkeypatch.setenv("RLM_INDEX_DIR", str(project / ".idx_nosyn"))
        builder = IndexBuilder()
        db_path = builder.build(
            str(project),
            build_calls=False,
            build_fts=True,
            build_synonyms=False,  # no synonyms
        )
        reader = IndexReader(str(db_path))
        bsl = make_bsl_helpers(
            base_path=str(project),
            resolve_safe=lambda p: __import__("pathlib").Path(p),
            read_file_fn=lambda p: "",
            grep_fn=lambda pat, path="": [],
            glob_files_fn=lambda pat: [],
            idx_reader=reader,
        )
        results = bsl["search"]("Заполнить", limit=30)
        reader.close()
        # Should still get methods and/or regions — just no objects
        source_types = {r["source_type"] for r in results}
        assert "object" not in source_types
        assert len(results) >= 1

    def test_search_no_fts(self, tmp_path, monkeypatch):
        """Index with build_fts=False — scope='methods' returns []."""
        project = _create_full_fixture(tmp_path)
        monkeypatch.setenv("RLM_INDEX_DIR", str(project / ".idx_nofts"))
        builder = IndexBuilder()
        db_path = builder.build(
            str(project),
            build_calls=False,
            build_fts=False,
            build_synonyms=True,
        )
        reader = IndexReader(str(db_path))
        bsl = make_bsl_helpers(
            base_path=str(project),
            resolve_safe=lambda p: __import__("pathlib").Path(p),
            read_file_fn=lambda p: "",
            grep_fn=lambda pat, path="": [],
            glob_files_fn=lambda pat: [],
            idx_reader=reader,
        )
        methods = bsl["search"]("Заполнить", scope="methods")
        assert methods == []

        # Other scopes should still work
        objects = bsl["search"]("Заполнение", scope="objects")
        assert len(objects) >= 1
        reader.close()


# ---------------------------------------------------------------------------
# Registration and strategy
# ---------------------------------------------------------------------------


class TestSearchRegistration:
    def test_search_in_registry(self, full_index):
        bsl, _ = full_index
        assert "search" in bsl["_registry"]
        assert bsl["_registry"]["search"]["cat"] == "discovery"

    def test_search_in_strategy(self, full_index):
        _, reader = full_index
        from rlm_tools_bsl.bsl_knowledge import get_strategy

        stats = reader.get_statistics()
        strategy = get_strategy(effort="medium", format_info=None, idx_stats=stats)
        assert "search(" in strategy
