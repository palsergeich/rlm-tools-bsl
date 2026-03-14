import os
import tempfile

from rlm_tools_bsl.helpers import make_helpers
from rlm_tools_bsl.format_detector import FormatInfo, SourceFormat, detect_format
from rlm_tools_bsl.bsl_helpers import make_bsl_helpers, parse_metadata_xml


BSL_CODE = """\
#Область ПрограммныйИнтерфейс

Процедура ЗаполнитьДанные(Параметр1, Параметр2) Экспорт
    // тело процедуры
    Сообщить("Начало заполнения");
КонецПроцедуры

Функция ПолучитьСумму(Сумма1, Сумма2) Экспорт
    Возврат Сумма1 + Сумма2;
КонецФункции

Процедура ВнутренняяПроцедура()
    // внутренняя
КонецПроцедуры

#КонецОбласти
"""

BSL_CALLER_CODE = """\
Процедура ОбработкаЗаполнения() Экспорт
    МойМодуль.ЗаполнитьДанные(1, 2);
КонецПроцедуры

Процедура НеВызывает()
    // ЗаполнитьДанные(1, 2);
    //ЗаполнитьДанные(1, 2);
    Сообщить("ЗаполнитьДанные");
КонецПроцедуры
"""


def _create_cf_fixture(tmpdir):
    """Create a CF-style structure with BSL files."""
    # CommonModules/МойМодуль/Ext/Module.bsl
    mod_dir = os.path.join(tmpdir, "CommonModules", "МойМодуль", "Ext")
    os.makedirs(mod_dir)
    with open(os.path.join(mod_dir, "Module.bsl"), "w", encoding="utf-8") as f:
        f.write(BSL_CODE)

    # Documents/АвансовыйОтчет/Ext/ObjectModule.bsl
    doc_dir = os.path.join(tmpdir, "Documents", "АвансовыйОтчет", "Ext")
    os.makedirs(doc_dir)
    with open(os.path.join(doc_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
        f.write(BSL_CALLER_CODE)

    # Documents/АвансовыйОтчет/Forms/ФормаДокумента/Ext/Form/Module.bsl
    form_dir = os.path.join(tmpdir, "Documents", "АвансовыйОтчет", "Forms", "ФормаДокумента", "Ext", "Form")
    os.makedirs(form_dir)
    with open(os.path.join(form_dir, "Module.bsl"), "w", encoding="utf-8") as f:
        f.write("// form module code\n")

    # Configuration.xml
    with open(os.path.join(tmpdir, "Configuration.xml"), "w") as f:
        f.write("<Configuration/>")


def _make_bsl_fixture(tmpdir):
    """Create fixture and return bsl_helpers dict."""
    _create_cf_fixture(tmpdir)
    helpers, resolve_safe = make_helpers(tmpdir)
    format_info = detect_format(tmpdir)
    bsl = make_bsl_helpers(
        base_path=tmpdir,
        resolve_safe=resolve_safe,
        read_file_fn=helpers["read_file"],
        grep_fn=helpers["grep"],
        glob_files_fn=helpers["glob_files"],
        format_info=format_info,
    )
    return bsl, helpers


# --- find_module ---

def test_find_module_by_name():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_module"]("МойМодуль")
        assert len(results) >= 1
        assert any(r["object_name"] == "МойМодуль" for r in results)


def test_find_module_by_path_fragment():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_module"]("АвансовыйОтчет")
        assert len(results) >= 1


def test_find_module_case_insensitive():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_module"]("моймодуль")
        assert len(results) >= 1


def test_find_module_no_results():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_module"]("НесуществующийМодуль")
        assert len(results) == 0


# --- find_by_type ---

def test_find_by_type():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_by_type"]("Documents")
        assert len(results) >= 1
        assert all(r["category"] == "Documents" for r in results)


def test_find_by_type_with_name():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_by_type"]("CommonModules", "МойМодуль")
        assert len(results) >= 1


# --- extract_procedures ---

def test_extract_procedures():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        # Find the module path first
        modules = bsl["find_module"]("МойМодуль")
        assert len(modules) >= 1
        path = modules[0]["path"]

        procs = bsl["extract_procedures"](path)
        assert len(procs) == 3
        names = [p["name"] for p in procs]
        assert "ЗаполнитьДанные" in names
        assert "ПолучитьСумму" in names
        assert "ВнутренняяПроцедура" in names


def test_extract_procedures_export_flag():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        modules = bsl["find_module"]("МойМодуль")
        path = modules[0]["path"]

        procs = bsl["extract_procedures"](path)
        by_name = {p["name"]: p for p in procs}
        assert by_name["ЗаполнитьДанные"]["is_export"] is True
        assert by_name["ПолучитьСумму"]["is_export"] is True
        assert by_name["ВнутренняяПроцедура"]["is_export"] is False


def test_extract_procedures_has_end_line():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        modules = bsl["find_module"]("МойМодуль")
        path = modules[0]["path"]

        procs = bsl["extract_procedures"](path)
        for p in procs:
            assert p["end_line"] is not None
            assert p["end_line"] > p["line"]


# --- find_exports ---

def test_find_exports():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        modules = bsl["find_module"]("МойМодуль")
        path = modules[0]["path"]

        exports = bsl["find_exports"](path)
        assert len(exports) == 2
        names = [e["name"] for e in exports]
        assert "ЗаполнитьДанные" in names
        assert "ПолучитьСумму" in names
        assert "ВнутренняяПроцедура" not in names


# --- safe_grep ---

def test_safe_grep_with_hint():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["safe_grep"]("ЗаполнитьДанные", name_hint="АвансовыйОтчет")
        assert len(results) >= 1


def test_safe_grep_without_hint():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["safe_grep"]("Процедура")
        assert len(results) >= 1


# --- read_procedure ---

def test_read_procedure():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        modules = bsl["find_module"]("МойМодуль")
        path = modules[0]["path"]

        body = bsl["read_procedure"](path, "ЗаполнитьДанные")
        assert body is not None
        assert "ЗаполнитьДанные" in body
        assert "КонецПроцедуры" in body


def test_read_procedure_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        modules = bsl["find_module"]("МойМодуль")
        path = modules[0]["path"]

        body = bsl["read_procedure"](path, "НесуществующаяПроцедура")
        assert body is None


# --- find_callers ---

def test_find_callers():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_callers"]("ЗаполнитьДанные")
        assert len(results) >= 1
        # Should find the call in АвансовыйОтчет
        assert any("АвансовыйОтчет" in r.get("file", "") for r in results)


def test_find_callers_with_hint():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        results = bsl["find_callers"]("ЗаполнитьДанные", module_hint="АвансовыйОтчет")
        assert len(results) >= 1


# --- parse_metadata_xml / parse_object_xml ---

CATALOG_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
    xmlns:v8="http://v8.1c.ru/8.1/data/core"
    xmlns:xr="http://v8.1c.ru/8.3/xcf/readable">
<Catalog>
  <Properties>
    <Name>ВидыСпецодежды</Name>
    <Synonym>
      <v8:item>
        <v8:lang>ru</v8:lang>
        <v8:content>Виды спецодежды</v8:content>
      </v8:item>
    </Synonym>
  </Properties>
  <Attribute>
    <Properties>
      <Name>Безразмерный</Name>
      <Synonym>
        <v8:item>
          <v8:lang>ru</v8:lang>
          <v8:content>Безразмерный</v8:content>
        </v8:item>
      </Synonym>
      <Type>
        <v8:Type>xs:boolean</v8:Type>
      </Type>
    </Properties>
  </Attribute>
  <TabularSection>
    <Properties>
      <Name>Размеры</Name>
      <Synonym>
        <v8:item>
          <v8:lang>ru</v8:lang>
          <v8:content>Размеры</v8:content>
        </v8:item>
      </Synonym>
    </Properties>
    <Attribute>
      <Properties>
        <Name>Размер</Name>
        <Synonym>
          <v8:item>
            <v8:lang>ru</v8:lang>
            <v8:content>Размер</v8:content>
          </v8:item>
        </Synonym>
        <Type>
          <v8:Type>xs:string</v8:Type>
        </Type>
      </Properties>
    </Attribute>
  </TabularSection>
</Catalog>
</MetaDataObject>
"""

SUBSYSTEM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
    xmlns:v8="http://v8.1c.ru/8.1/data/core"
    xmlns:xr="http://v8.1c.ru/8.3/xcf/readable">
<Subsystem>
  <Properties>
    <Name>Спецодежда</Name>
    <Synonym>
      <v8:item>
        <v8:lang>ru</v8:lang>
        <v8:content>Спецодежда (лтх)</v8:content>
      </v8:item>
    </Synonym>
    <Content>
      <xr:Item>Catalog.лтхВидыСпецодежды</xr:Item>
      <xr:Item>Document.лтхЗаявкаНаВыдачуСпецодежды</xr:Item>
    </Content>
  </Properties>
</Subsystem>
</MetaDataObject>
"""

REGISTER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
    xmlns:v8="http://v8.1c.ru/8.1/data/core">
<AccumulationRegister>
  <Properties>
    <Name>ЗаказыНаВыдачу</Name>
    <Synonym>
      <v8:item>
        <v8:lang>ru</v8:lang>
        <v8:content>Заказы на выдачу спецодежды</v8:content>
      </v8:item>
    </Synonym>
  </Properties>
  <Dimension>
    <Properties>
      <Name>ВидСпецодежды</Name>
      <Synonym>
        <v8:item>
          <v8:lang>ru</v8:lang>
          <v8:content>Вид спецодежды</v8:content>
        </v8:item>
      </Synonym>
      <Type>
        <v8:Type>CatalogRef.лтхВидыСпецодежды</v8:Type>
      </Type>
    </Properties>
  </Dimension>
  <Resource>
    <Properties>
      <Name>Количество</Name>
      <Synonym>
        <v8:item>
          <v8:lang>ru</v8:lang>
          <v8:content>Количество</v8:content>
        </v8:item>
      </Synonym>
      <Type>
        <v8:Type>xs:decimal</v8:Type>
      </Type>
    </Properties>
  </Resource>
</AccumulationRegister>
</MetaDataObject>
"""


# --- MDO format test data ---

MDO_DOCUMENT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Document xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"
    xmlns:core="http://g5.1c.ru/v8/dt/mcore"
    uuid="abcd1234-0000-0000-0000-000000000001">
  <name>ЗаявкаНаВыдачу</name>
  <synonym>
    <key>ru</key>
    <value>Заявка на выдачу спецодежды</value>
  </synonym>
  <attributes uuid="abcd1234-0000-0000-0000-000000000002">
    <name>ФизЛицо</name>
    <synonym>
      <key>ru</key>
      <value>Физическое лицо</value>
    </synonym>
    <type>
      <types>CatalogRef.ФизическиеЛица</types>
    </type>
  </attributes>
  <attributes uuid="abcd1234-0000-0000-0000-000000000003">
    <name>Организация</name>
    <synonym>
      <key>ru</key>
      <value>Организация</value>
    </synonym>
    <type>
      <types>CatalogRef.Организации</types>
    </type>
  </attributes>
  <tabularSections uuid="abcd1234-0000-0000-0000-000000000010">
    <name>ВидыСпецодежды</name>
    <synonym>
      <key>ru</key>
      <value>Виды спецодежды</value>
    </synonym>
    <attributes uuid="abcd1234-0000-0000-0000-000000000011">
      <name>ВидСпецодежды</name>
      <synonym>
        <key>ru</key>
        <value>Вид спецодежды</value>
      </synonym>
      <type>
        <types>CatalogRef.ВидыСпецодежды</types>
      </type>
    </attributes>
    <attributes uuid="abcd1234-0000-0000-0000-000000000012">
      <name>Количество</name>
      <type>
        <types>Number</types>
      </type>
    </attributes>
  </tabularSections>
  <forms>ФормаДокумента</forms>
  <forms>ФормаСписка</forms>
  <commands>Печать</commands>
</mdclass:Document>
"""

MDO_REGISTER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mdclass:AccumulationRegister xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"
    uuid="abcd1234-0000-0000-0000-000000000020">
  <name>ТоварыНаСкладах</name>
  <synonym>
    <key>ru</key>
    <value>Товары на складах</value>
  </synonym>
  <dimensions uuid="abcd1234-0000-0000-0000-000000000021">
    <name>Номенклатура</name>
    <synonym>
      <key>ru</key>
      <value>Номенклатура</value>
    </synonym>
    <type>
      <types>CatalogRef.Номенклатура</types>
    </type>
  </dimensions>
  <dimensions uuid="abcd1234-0000-0000-0000-000000000022">
    <name>Склад</name>
    <type>
      <types>CatalogRef.Склады</types>
    </type>
  </dimensions>
  <resources uuid="abcd1234-0000-0000-0000-000000000023">
    <name>Количество</name>
    <type>
      <types>Number</types>
    </type>
  </resources>
</mdclass:AccumulationRegister>
"""

MDO_SUBSYSTEM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Subsystem xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"
    uuid="abcd1234-0000-0000-0000-000000000030">
  <name>Спецодежда</name>
  <synonym>
    <key>ru</key>
    <value>Спецодежда</value>
  </synonym>
  <content>Catalog.ВидыСпецодежды</content>
  <content>Document.ЗаявкаНаВыдачу</content>
  <content>AccumulationRegister.ТоварыНаСкладах</content>
</mdclass:Subsystem>
"""


def test_parse_catalog_xml():
    result = parse_metadata_xml(CATALOG_XML)
    assert result["object_type"] == "Catalog"
    assert result["name"] == "ВидыСпецодежды"
    assert result["synonym"] == "Виды спецодежды"
    assert len(result["attributes"]) == 1
    assert result["attributes"][0]["name"] == "Безразмерный"
    assert result["attributes"][0]["type"] == "xs:boolean"
    assert len(result["tabular_sections"]) == 1
    ts = result["tabular_sections"][0]
    assert ts["name"] == "Размеры"
    assert len(ts["attributes"]) == 1
    assert ts["attributes"][0]["name"] == "Размер"


def test_parse_subsystem_xml():
    result = parse_metadata_xml(SUBSYSTEM_XML)
    assert result["object_type"] == "Subsystem"
    assert result["name"] == "Спецодежда"
    assert "content" in result
    assert "Catalog.лтхВидыСпецодежды" in result["content"]
    assert "Document.лтхЗаявкаНаВыдачуСпецодежды" in result["content"]


def test_parse_register_xml():
    result = parse_metadata_xml(REGISTER_XML)
    assert result["object_type"] == "AccumulationRegister"
    assert result["name"] == "ЗаказыНаВыдачу"
    assert len(result["dimensions"]) == 1
    assert result["dimensions"][0]["name"] == "ВидСпецодежды"
    assert len(result["resources"]) == 1
    assert result["resources"][0]["name"] == "Количество"


def test_parse_object_xml_via_sandbox():
    """Test parse_object_xml as registered in sandbox helpers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _create_cf_fixture(tmpdir)
        # Write a metadata XML file
        xml_dir = os.path.join(tmpdir, "Catalogs", "ВидыСО")
        os.makedirs(xml_dir)
        xml_path = os.path.join(xml_dir, "Ext", "ObjectModule.bsl")
        # Write the XML at the catalog level
        with open(os.path.join(xml_dir, "ВидыСО.xml"), "w", encoding="utf-8") as f:
            f.write(CATALOG_XML)

        helpers, resolve_safe = make_helpers(tmpdir)
        format_info = detect_format(tmpdir)
        bsl = make_bsl_helpers(
            base_path=tmpdir,
            resolve_safe=resolve_safe,
            read_file_fn=helpers["read_file"],
            grep_fn=helpers["grep"],
            glob_files_fn=helpers["glob_files"],
            format_info=format_info,
        )
        # Use relative path
        result = bsl["parse_object_xml"]("Catalogs/ВидыСО/ВидыСО.xml")
        assert result["object_type"] == "Catalog"
        assert result["name"] == "ВидыСпецодежды"


# --- MDO format tests ---

def test_parse_mdo_document():
    result = parse_metadata_xml(MDO_DOCUMENT_XML)
    assert result["object_type"] == "Document"
    assert result["name"] == "ЗаявкаНаВыдачу"
    assert result["synonym"] == "Заявка на выдачу спецодежды"
    # Attributes
    assert len(result["attributes"]) == 2
    assert result["attributes"][0]["name"] == "ФизЛицо"
    assert result["attributes"][0]["synonym"] == "Физическое лицо"
    assert result["attributes"][0]["type"] == "CatalogRef.ФизическиеЛица"
    assert result["attributes"][1]["name"] == "Организация"
    # Tabular section
    assert len(result["tabular_sections"]) == 1
    ts = result["tabular_sections"][0]
    assert ts["name"] == "ВидыСпецодежды"
    assert ts["synonym"] == "Виды спецодежды"
    assert len(ts["attributes"]) == 2
    assert ts["attributes"][0]["name"] == "ВидСпецодежды"
    assert ts["attributes"][1]["name"] == "Количество"
    # Forms and commands
    assert result["forms"] == ["ФормаДокумента", "ФормаСписка"]
    assert result["commands"] == ["Печать"]


def test_parse_mdo_register():
    result = parse_metadata_xml(MDO_REGISTER_XML)
    assert result["object_type"] == "AccumulationRegister"
    assert result["name"] == "ТоварыНаСкладах"
    assert result["synonym"] == "Товары на складах"
    assert len(result["dimensions"]) == 2
    assert result["dimensions"][0]["name"] == "Номенклатура"
    assert result["dimensions"][0]["type"] == "CatalogRef.Номенклатура"
    assert result["dimensions"][1]["name"] == "Склад"
    assert len(result["resources"]) == 1
    assert result["resources"][0]["name"] == "Количество"


def test_parse_mdo_subsystem():
    result = parse_metadata_xml(MDO_SUBSYSTEM_XML)
    assert result["object_type"] == "Subsystem"
    assert result["name"] == "Спецодежда"
    assert len(result["content"]) == 3
    assert "Catalog.ВидыСпецодежды" in result["content"]
    assert "Document.ЗаявкаНаВыдачу" in result["content"]
    assert "AccumulationRegister.ТоварыНаСкладах" in result["content"]


# --- find_callers_context ---

def test_find_callers_context_basic():
    """Basic: finds caller with all required fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ЗаполнитьДанные")
        callers = result["callers"]
        assert len(callers) >= 1
        c = callers[0]
        # All required fields present
        assert "file" in c
        assert "caller_name" in c
        assert "caller_is_export" in c
        assert "line" in c
        assert "context" in c
        assert "object_name" in c
        assert "category" in c
        assert "module_type" in c
        # Caller is ОбработкаЗаполнения
        assert c["caller_name"] == "ОбработкаЗаполнения"


def test_find_callers_context_with_hint():
    """With module_hint: determines export scope, finds caller across files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ЗаполнитьДанные", module_hint="МойМодуль")
        callers = result["callers"]
        assert len(callers) >= 1
        assert any(c["caller_name"] == "ОбработкаЗаполнения" for c in callers)


def test_find_callers_context_no_callers():
    """Internal procedure with no callers returns empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ВнутренняяПроцедура")
        assert result["callers"] == []


def test_find_callers_context_ignores_comments():
    """Calls in comments (// with and without space) should be ignored."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ЗаполнитьДанные")
        caller_names = [c["caller_name"] for c in result["callers"]]
        assert "НеВызывает" not in caller_names


def test_find_callers_context_ignores_strings():
    """Calls inside string literals should be ignored."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ЗаполнитьДанные")
        caller_names = [c["caller_name"] for c in result["callers"]]
        # НеВызывает has the name only in a string literal (after comment lines are stripped)
        assert "НеВызывает" not in caller_names


def test_find_callers_context_caller_metadata():
    """Verify caller metadata: category, object_name, module_type."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ЗаполнитьДанные")
        callers = result["callers"]
        c = next(c for c in callers if c["caller_name"] == "ОбработкаЗаполнения")
        assert c["category"] == "Documents"
        assert c["object_name"] == "АвансовыйОтчет"
        assert c["module_type"] == "ObjectModule"


def test_find_callers_context_qualified_call():
    """Qualified call МойМодуль.ЗаполнитьДанные() is found by proc name alone."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ЗаполнитьДанные")
        callers = result["callers"]
        # The call is МойМодуль.ЗаполнитьДанные(1, 2) — should be found
        assert any(
            "МойМодуль.ЗаполнитьДанные" in c["context"]
            for c in callers
        )


def test_find_callers_context_meta():
    """Result contains _meta with total_files, scanned_files, has_more."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["find_callers_context"]("ЗаполнитьДанные")
        meta = result["_meta"]
        assert "total_files" in meta
        assert "scanned_files" in meta
        assert "has_more" in meta
        assert meta["has_more"] is False  # small fixture, all scanned


def test_find_callers_context_pagination():
    """Pagination: limit=1 → has_more=True, offset=1 → next batch."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        # First page: limit=1
        result1 = bsl["find_callers_context"]("ЗаполнитьДанные", limit=1)
        meta1 = result1["_meta"]
        if meta1["total_files"] > 1:
            assert meta1["has_more"] is True
            # Second page
            result2 = bsl["find_callers_context"]("ЗаполнитьДанные", offset=1, limit=1)
            assert result2["_meta"]["scanned_files"] >= 1
        else:
            # Only 1 file contains it — pagination not applicable, still valid
            assert meta1["has_more"] is False


# --- Composite helpers ---


SUBSYSTEM_CF_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
                xmlns:v8="http://v8.1c.ru/8.1/data/core"
                xmlns:xr="http://v8.1c.ru/8.3/xcf/readable">
<Subsystem>
<Properties>
<Name>лтхСпецодежда</Name>
<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Спецодежда</v8:content></v8:item></Synonym>
<Content>
<xr:Item>Catalog.лтхВидыСпецодежды</xr:Item>
<xr:Item>Document.ВнутреннееПотребление</xr:Item>
<xr:Item>Document.лтхЗаявкаНаВыдачуСпецодежды</xr:Item>
</Content>
</Properties>
</Subsystem>
</MetaDataObject>
"""


def _make_subsystem_fixture(tmpdir):
    """Create fixture with a subsystem XML."""
    # Add subsystem XML to existing fixture
    sub_dir = os.path.join(
        tmpdir, "Subsystems", "Администрирование", "Subsystems", "лтхСпецодежда",
    )
    os.makedirs(sub_dir, exist_ok=True)
    with open(os.path.join(sub_dir, "лтхСпецодежда.xml"), "w", encoding="utf-8") as f:
        f.write(SUBSYSTEM_CF_XML)
    # Now create the rest of the fixture (BSL files, Configuration.xml)
    helpers, resolve_safe = make_helpers(tmpdir)
    # Create CF structure manually (avoid _create_cf_fixture which fails on existing dirs)
    mod_dir = os.path.join(tmpdir, "CommonModules", "МойМодуль", "Ext")
    os.makedirs(mod_dir, exist_ok=True)
    with open(os.path.join(mod_dir, "Module.bsl"), "w", encoding="utf-8") as f:
        f.write(BSL_CODE)
    doc_dir = os.path.join(tmpdir, "Documents", "АвансовыйОтчет", "Ext")
    os.makedirs(doc_dir, exist_ok=True)
    with open(os.path.join(doc_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
        f.write(BSL_CALLER_CODE)
    with open(os.path.join(tmpdir, "Configuration.xml"), "w") as f:
        f.write("<Configuration/>")
    format_info = detect_format(tmpdir)
    bsl = make_bsl_helpers(
        base_path=tmpdir,
        resolve_safe=resolve_safe,
        read_file_fn=helpers["read_file"],
        grep_fn=helpers["grep"],
        glob_files_fn=helpers["glob_files"],
        format_info=format_info,
    )
    return bsl, helpers


def test_analyze_subsystem_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_subsystem_fixture(tmpdir)
        result = bsl["analyze_subsystem"]("Спецодежда")
        assert result["subsystems_found"] >= 1
        sub = result["subsystems"][0]
        assert sub["synonym"] == "Спецодежда"
        assert len(sub["custom_objects"]) >= 1
        custom_names = [o["name"] for o in sub["custom_objects"]]
        assert "лтхВидыСпецодежды" in custom_names
        standard_names = [o["name"] for o in sub["standard_objects"]]
        assert "ВнутреннееПотребление" in standard_names


def test_analyze_subsystem_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["analyze_subsystem"]("НесуществующаяПодсистема")
        assert "error" in result


BSL_CUSTOM_CODE = """\
#Область ИРИС

Процедура лтхОбработкаСпецодежды() Экспорт
    // нетиповая процедура
КонецПроцедуры

Процедура ТиповаяПроцедура()
    // типовая
КонецПроцедуры

#КонецОбласти
"""


def test_find_custom_modifications():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create object with custom code
        doc_dir = os.path.join(tmpdir, "Documents", "ТестДок", "Ext")
        os.makedirs(doc_dir)
        with open(os.path.join(doc_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
            f.write(BSL_CUSTOM_CODE)
        with open(os.path.join(tmpdir, "Configuration.xml"), "w") as f:
            f.write("<Configuration/>")

        helpers, resolve_safe = make_helpers(tmpdir)
        format_info = detect_format(tmpdir)
        bsl = make_bsl_helpers(
            base_path=tmpdir,
            resolve_safe=resolve_safe,
            read_file_fn=helpers["read_file"],
            grep_fn=helpers["grep"],
            glob_files_fn=helpers["glob_files"],
            format_info=format_info,
        )

        result = bsl["find_custom_modifications"]("ТестДок")
        assert result["modules_analyzed"] >= 1
        assert len(result["modifications"]) >= 1
        mod = result["modifications"][0]
        custom_proc_names = [p["name"] for p in mod["custom_procedures"]]
        assert "лтхОбработкаСпецодежды" in custom_proc_names
        assert "ТиповаяПроцедура" not in custom_proc_names
        region_names = [r["name"] for r in mod["custom_regions"]]
        assert "ИРИС" in region_names


def test_analyze_object():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        result = bsl["analyze_object"]("МойМодуль")
        assert result["name"] == "МойМодуль"
        assert result["category"] == "CommonModules"
        assert len(result["modules"]) >= 1
        mod = result["modules"][0]
        assert mod["procedures_count"] == 3
        assert mod["exports_count"] == 2
