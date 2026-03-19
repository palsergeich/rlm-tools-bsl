import os
import tempfile

from rlm_tools_bsl.helpers import make_helpers
from rlm_tools_bsl.format_detector import FormatInfo, SourceFormat, detect_format
from rlm_tools_bsl.bsl_helpers import (
    make_bsl_helpers,
    parse_metadata_xml,
    parse_event_subscription_xml,
    parse_scheduled_job_xml,
    parse_enum_xml,
    parse_functional_option_xml,
    parse_rights_xml,
)


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

def test_find_module_by_name(bsl_env):
    results = bsl_env.bsl["find_module"]("МойМодуль")
    assert len(results) >= 1
    assert any(r["object_name"] == "МойМодуль" for r in results)


def test_find_module_by_path_fragment(bsl_env):
    results = bsl_env.bsl["find_module"]("АвансовыйОтчет")
    assert len(results) >= 1


def test_find_module_case_insensitive(bsl_env):
    results = bsl_env.bsl["find_module"]("моймодуль")
    assert len(results) >= 1


def test_find_module_no_results(bsl_env):
    results = bsl_env.bsl["find_module"]("НесуществующийМодуль")
    assert len(results) == 0


# --- find_by_type ---

def test_find_by_type(bsl_env):
    results = bsl_env.bsl["find_by_type"]("Documents")
    assert len(results) >= 1
    assert all(r["category"] == "Documents" for r in results)


def test_find_by_type_with_name(bsl_env):
    results = bsl_env.bsl["find_by_type"]("CommonModules", "МойМодуль")
    assert len(results) >= 1


# --- extract_procedures ---

def test_extract_procedures(bsl_env):
    # Find the module path first
    modules = bsl_env.bsl["find_module"]("МойМодуль")
    assert len(modules) >= 1
    path = modules[0]["path"]

    procs = bsl_env.bsl["extract_procedures"](path)
    assert len(procs) == 3
    names = [p["name"] for p in procs]
    assert "ЗаполнитьДанные" in names
    assert "ПолучитьСумму" in names
    assert "ВнутренняяПроцедура" in names


def test_extract_procedures_export_flag(bsl_env):
    modules = bsl_env.bsl["find_module"]("МойМодуль")
    path = modules[0]["path"]

    procs = bsl_env.bsl["extract_procedures"](path)
    by_name = {p["name"]: p for p in procs}
    assert by_name["ЗаполнитьДанные"]["is_export"] is True
    assert by_name["ПолучитьСумму"]["is_export"] is True
    assert by_name["ВнутренняяПроцедура"]["is_export"] is False


def test_extract_procedures_has_end_line(bsl_env):
    modules = bsl_env.bsl["find_module"]("МойМодуль")
    path = modules[0]["path"]

    procs = bsl_env.bsl["extract_procedures"](path)
    for p in procs:
        assert p["end_line"] is not None
        assert p["end_line"] > p["line"]


# --- find_exports ---

def test_find_exports(bsl_env):
    modules = bsl_env.bsl["find_module"]("МойМодуль")
    path = modules[0]["path"]

    exports = bsl_env.bsl["find_exports"](path)
    assert len(exports) == 2
    names = [e["name"] for e in exports]
    assert "ЗаполнитьДанные" in names
    assert "ПолучитьСумму" in names
    assert "ВнутренняяПроцедура" not in names


# --- safe_grep ---

def test_safe_grep_with_hint(bsl_env):
    results = bsl_env.bsl["safe_grep"]("ЗаполнитьДанные", name_hint="АвансовыйОтчет")
    assert len(results) >= 1


def test_safe_grep_without_hint(bsl_env):
    results = bsl_env.bsl["safe_grep"]("Процедура")
    assert len(results) >= 1


def test_safe_grep_parallel_order(bsl_env):
    """Parallel safe_grep returns results sorted by (file, line)."""
    results = bsl_env.bsl["safe_grep"]("Процедура", max_files=50)
    assert len(results) >= 1
    # Verify sort order: (file, line) ascending
    for i in range(1, len(results)):
        prev = (results[i - 1].get("file", ""), results[i - 1].get("line", 0))
        curr = (results[i].get("file", ""), results[i].get("line", 0))
        assert prev <= curr, f"Order violation: {prev} > {curr}"


# --- read_procedure ---

def test_read_procedure(bsl_env):
    modules = bsl_env.bsl["find_module"]("МойМодуль")
    path = modules[0]["path"]

    body = bsl_env.bsl["read_procedure"](path, "ЗаполнитьДанные")
    assert body is not None
    assert "ЗаполнитьДанные" in body
    assert "КонецПроцедуры" in body


def test_read_procedure_not_found(bsl_env):
    modules = bsl_env.bsl["find_module"]("МойМодуль")
    path = modules[0]["path"]

    body = bsl_env.bsl["read_procedure"](path, "НесуществующаяПроцедура")
    assert body is None


# --- find_callers ---

def test_find_callers(bsl_env):
    results = bsl_env.bsl["find_callers"]("ЗаполнитьДанные")
    assert len(results) >= 1
    # Should find the call in АвансовыйОтчет
    assert any("АвансовыйОтчет" in r.get("file", "") for r in results)


def test_find_callers_with_hint(bsl_env):
    results = bsl_env.bsl["find_callers"]("ЗаполнитьДанные", module_hint="АвансовыйОтчет")
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
        <v8:content>Спецодежда (ктн)</v8:content>
      </v8:item>
    </Synonym>
    <Content>
      <xr:Item>Catalog.ктнВидыСпецодежды</xr:Item>
      <xr:Item>Document.ктнЗаявкаНаВыдачуСпецодежды</xr:Item>
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
        <v8:Type>CatalogRef.ктнВидыСпецодежды</v8:Type>
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
    assert "Catalog.ктнВидыСпецодежды" in result["content"]
    assert "Document.ктнЗаявкаНаВыдачуСпецодежды" in result["content"]


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


def test_parse_object_xml_directory_path():
    """Test parse_object_xml with a directory path (auto-resolves to XML)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _create_cf_fixture(tmpdir)
        # Create CF-style Document metadata: Documents/TestDoc/Ext/Document.xml
        doc_xml_dir = os.path.join(tmpdir, "Documents", "АвансовыйОтчет", "Ext")
        os.makedirs(doc_xml_dir, exist_ok=True)
        with open(os.path.join(doc_xml_dir, "Document.xml"), "w", encoding="utf-8") as f:
            f.write(CATALOG_XML)  # reuse catalog XML, structure is similar enough

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
        # Pass directory path — should auto-resolve to Ext/Document.xml
        result = bsl["parse_object_xml"]("Documents/АвансовыйОтчет")
        assert "name" in result
        assert result["name"] == "ВидыСпецодежды"  # from CATALOG_XML fixture


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

def test_find_callers_context_basic(bsl_env):
    """Basic: finds caller with all required fields."""
    result = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные")
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


def test_find_callers_context_with_hint(bsl_env):
    """With module_hint: determines export scope, finds caller across files."""
    result = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные", module_hint="МойМодуль")
    callers = result["callers"]
    assert len(callers) >= 1
    assert any(c["caller_name"] == "ОбработкаЗаполнения" for c in callers)


def test_find_callers_context_no_callers(bsl_env):
    """Internal procedure with no callers returns empty list."""
    result = bsl_env.bsl["find_callers_context"]("ВнутренняяПроцедура")
    assert result["callers"] == []


def test_find_callers_context_ignores_comments(bsl_env):
    """Calls in comments (// with and without space) should be ignored."""
    result = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные")
    caller_names = [c["caller_name"] for c in result["callers"]]
    assert "НеВызывает" not in caller_names


def test_find_callers_context_ignores_strings(bsl_env):
    """Calls inside string literals should be ignored."""
    result = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные")
    caller_names = [c["caller_name"] for c in result["callers"]]
    # НеВызывает has the name only in a string literal (after comment lines are stripped)
    assert "НеВызывает" not in caller_names


def test_find_callers_context_caller_metadata(bsl_env):
    """Verify caller metadata: category, object_name, module_type."""
    result = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные")
    callers = result["callers"]
    c = next(c for c in callers if c["caller_name"] == "ОбработкаЗаполнения")
    assert c["category"] == "Documents"
    assert c["object_name"] == "АвансовыйОтчет"
    assert c["module_type"] == "ObjectModule"


def test_find_callers_context_qualified_call(bsl_env):
    """Qualified call МойМодуль.ЗаполнитьДанные() is found by proc name alone."""
    result = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные")
    callers = result["callers"]
    # The call is МойМодуль.ЗаполнитьДанные(1, 2) — should be found
    assert any(
        "МойМодуль.ЗаполнитьДанные" in c["context"]
        for c in callers
    )


def test_find_callers_context_meta(bsl_env):
    """Result contains _meta with total_files, scanned_files, has_more."""
    result = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные")
    meta = result["_meta"]
    assert "total_files" in meta
    assert "scanned_files" in meta
    assert "has_more" in meta
    assert meta["has_more"] is False  # small fixture, all scanned


def test_find_callers_context_pagination(bsl_env):
    """Pagination: limit=1 → has_more=True, offset=1 → next batch."""
    # First page: limit=1
    result1 = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные", limit=1)
    meta1 = result1["_meta"]
    if meta1["total_files"] > 1:
        assert meta1["has_more"] is True
        # Second page
        result2 = bsl_env.bsl["find_callers_context"]("ЗаполнитьДанные", offset=1, limit=1)
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
<Name>ктнСпецодежда</Name>
<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Спецодежда</v8:content></v8:item></Synonym>
<Content>
<xr:Item>Catalog.ктнВидыСпецодежды</xr:Item>
<xr:Item>Document.ВнутреннееПотребление</xr:Item>
<xr:Item>Document.ктнЗаявкаНаВыдачуСпецодежды</xr:Item>
</Content>
</Properties>
</Subsystem>
</MetaDataObject>
"""


def _make_subsystem_fixture(tmpdir):
    """Create fixture with a subsystem XML."""
    # Add subsystem XML to existing fixture
    sub_dir = os.path.join(
        tmpdir, "Subsystems", "Администрирование", "Subsystems", "ктнСпецодежда",
    )
    os.makedirs(sub_dir, exist_ok=True)
    with open(os.path.join(sub_dir, "ктнСпецодежда.xml"), "w", encoding="utf-8") as f:
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
        assert "ктнВидыСпецодежды" in custom_names
        standard_names = [o["name"] for o in sub["standard_objects"]]
        assert "ВнутреннееПотребление" in standard_names


def test_analyze_subsystem_not_found(bsl_env):
    result = bsl_env.bsl["analyze_subsystem"]("НесуществующаяПодсистема")
    assert "error" in result


BSL_CUSTOM_CODE = """\
#Область ктнДоработки

Процедура ктнОбработкаСпецодежды() Экспорт
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

        result = bsl["find_custom_modifications"]("ТестДок", custom_prefixes=["ктн"])
        assert result["modules_analyzed"] >= 1
        assert len(result["modifications"]) >= 1
        mod = result["modifications"][0]
        custom_proc_names = [p["name"] for p in mod["custom_procedures"]]
        assert "ктнОбработкаСпецодежды" in custom_proc_names
        assert "ТиповаяПроцедура" not in custom_proc_names
        region_names = [r["name"] for r in mod["custom_regions"]]
        assert "ктнДоработки" in region_names
        # Check prefix_source and prefixes_used in response
        assert result["prefix_source"] == "user"
        assert result["prefixes_used"] == ["ктн"]


def test_find_custom_modifications_parse_error():
    """parse_object_xml failure returns diagnostic parse_error field."""
    with tempfile.TemporaryDirectory() as tmpdir:
        doc_dir = os.path.join(tmpdir, "Documents", "ТестДок", "Ext")
        os.makedirs(doc_dir)
        with open(os.path.join(doc_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
            f.write("Процедура тст_Тест()\nКонецПроцедуры\n")
        # Write invalid XML so parse_object_xml fails
        with open(os.path.join(doc_dir, "Document.xml"), "w") as f:
            f.write("NOT-XML{{{{")
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

        result = bsl["find_custom_modifications"]("ТестДок", custom_prefixes=["тст"])
        assert "parse_error" in result
        assert result["modules_analyzed"] >= 1


def test_resolve_object_xml_edt_mdo():
    """_resolve_object_xml finds EDT-pattern {path}/{Name}.mdo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # EDT structure: Documents/ТестДок/ТестДок.mdo
        doc_dir = os.path.join(tmpdir, "Documents", "ТестДок")
        os.makedirs(doc_dir)
        mdo_path = os.path.join(doc_dir, "ТестДок.mdo")
        with open(mdo_path, "w", encoding="utf-8") as f:
            f.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<mdclass:Document xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"'
                ' uuid="00000000-0000-0000-0000-000000000001">\n'
                '  <name>ТестДок</name>\n'
                '</mdclass:Document>\n'
            )
        # Also create a BSL file so find_module works
        bsl_dir = os.path.join(doc_dir)
        with open(os.path.join(bsl_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
            f.write("Процедура Тест()\nКонецПроцедуры\n")
        with open(os.path.join(tmpdir, "Configuration.mdo"), "w", encoding="utf-8") as f:
            f.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<mdclass:Configuration xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"/>\n'
            )

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

        # _resolve_object_xml is internal, test through parse_object_xml
        result = bsl["parse_object_xml"]("Documents/ТестДок")
        # Should resolve to ТестДок.mdo and attempt to parse
        assert isinstance(result, dict)


def test_find_custom_modifications_extension_prefix_threshold():
    """Extension mode uses threshold=1 for prefix detection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create objects with a prefix that appears only once
        cat_dir = os.path.join(tmpdir, "Catalogs", "тст_Справочник", "Ext")
        os.makedirs(cat_dir)
        with open(os.path.join(cat_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
            f.write("Процедура тст_Метод()\nКонецПроцедуры\n")
        with open(os.path.join(tmpdir, "Configuration.xml"), "w") as f:
            f.write("<Configuration/>")

        helpers, resolve_safe = make_helpers(tmpdir)
        format_info = detect_format(tmpdir)

        # Without idx_reader (config_role unknown), threshold=3 → prefix "тст" won't be detected
        bsl = make_bsl_helpers(
            base_path=tmpdir,
            resolve_safe=resolve_safe,
            read_file_fn=helpers["read_file"],
            grep_fn=helpers["grep"],
            glob_files_fn=helpers["glob_files"],
            format_info=format_info,
        )
        auto_prefixes = bsl["_detected_prefixes"]()
        # Only 1 object with prefix тст → below threshold 3 → not detected
        assert "тст" not in auto_prefixes


def test_analyze_object(bsl_env):
    result = bsl_env.bsl["analyze_object"]("МойМодуль")
    assert result["name"] == "МойМодуль"
    assert result["category"] == "CommonModules"
    assert len(result["modules"]) >= 1
    mod = result["modules"][0]
    assert mod["procedures_count"] == 3
    assert mod["exports_count"] == 2


# === EventSubscription / ScheduledJob XML parsers ===

EVENT_SUB_CF_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
    xmlns:v8="http://v8.1c.ru/8.1/data/core"
    xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config">
<EventSubscription uuid="ba1f402d-0000-0000-0000-000000000001">
  <Properties>
    <Name>ЗаписатьВерсиюДокумента</Name>
    <Synonym>
      <v8:item><v8:lang>ru</v8:lang><v8:content>Записать версию документа</v8:content></v8:item>
    </Synonym>
    <Source>
      <v8:Type>cfg:DocumentObject.АвансовыйОтчет</v8:Type>
      <v8:Type>cfg:DocumentObject.ЗаказКлиента</v8:Type>
    </Source>
    <Event>BeforeWrite</Event>
    <Handler>CommonModule.ВерсионированиеОбъектовСобытия.ЗаписатьВерсиюДокумента</Handler>
  </Properties>
</EventSubscription>
</MetaDataObject>
"""

EVENT_SUB_MDO_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mdclass:EventSubscription xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"
    uuid="7ce50cee-0000-0000-0000-000000000001">
  <name>тст_ЗаписатьВерсиюДокумента</name>
  <synonym>
    <key>ru</key>
    <value>Записать версию документа</value>
  </synonym>
  <source>
    <types>DocumentObject.АвансовыйОтчет</types>
    <types>DocumentObject.СчетФактураВыданный</types>
  </source>
  <event>BeforeWrite</event>
  <handler>CommonModule.ВерсионированиеОбъектовСобытия.ЗаписатьВерсиюДокумента</handler>
</mdclass:EventSubscription>
"""

SCHEDULED_JOB_CF_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
    xmlns:v8="http://v8.1c.ru/8.1/data/core">
<ScheduledJob uuid="c7ffd8ab-0000-0000-0000-000000000001">
  <Properties>
    <Name>ЗагрузкаКурсовВалют</Name>
    <Synonym>
      <v8:item><v8:lang>ru</v8:lang><v8:content>Загрузка курсов валют</v8:content></v8:item>
    </Synonym>
    <MethodName>CommonModule.РаботаСКурсамиВалют.ЗагрузитьАктуальныйКурс</MethodName>
    <Use>false</Use>
    <Predefined>true</Predefined>
    <RestartCountOnFailure>10</RestartCountOnFailure>
    <RestartIntervalOnFailure>600</RestartIntervalOnFailure>
  </Properties>
</ScheduledJob>
</MetaDataObject>
"""

SCHEDULED_JOB_MDO_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mdclass:ScheduledJob xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"
    uuid="f3be2107-0000-0000-0000-000000000001">
  <name>ext_ОтправкаПодтверждения</name>
  <synonym>
    <key>ru</key>
    <value>Отправка подтверждения поставки</value>
  </synonym>
  <methodName>CommonModule.ext_РегламентныеЗадания.ОтправкаПодтверждения</methodName>
  <predefined>true</predefined>
  <restartCountOnFailure>3</restartCountOnFailure>
  <restartIntervalOnFailure>10</restartIntervalOnFailure>
</mdclass:ScheduledJob>
"""


ENUM_CF_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable">
  <Enum><Properties><Name>СтатусыЗаказов</Name><Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Статусы заказов</v8:content></v8:item></Synonym></Properties>
  <ChildObjects>
    <EnumValue><Properties><Name>Новый</Name><Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Новый</v8:content></v8:item></Synonym></Properties></EnumValue>
    <EnumValue><Properties><Name>ВРаботе</Name><Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>В работе</v8:content></v8:item></Synonym></Properties></EnumValue>
    <EnumValue><Properties><Name>Закрыт</Name><Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Закрыт</v8:content></v8:item></Synonym></Properties></EnumValue>
  </ChildObjects></Enum>
</MetaDataObject>
"""

ENUM_MDO_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Enum xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass">
  <name>ВажностьПроблемы</name>
  <enumValues><name>Предупреждение</name></enumValues>
  <enumValues><name>Ошибка</name></enumValues>
</mdclass:Enum>
"""

FUNCTIONAL_OPTION_CF_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable">
  <FunctionalOption><Properties><Name>ИспользоватьСерии</Name>
    <Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Использовать серии</v8:content></v8:item></Synonym>
    <Location>Constant.ИспользоватьСерии</Location>
    <Content><xr:Object>Document.ПриобретениеТоваров</xr:Object><xr:Object>Catalog.СерииНоменклатуры</xr:Object></Content>
  </Properties></FunctionalOption>
</MetaDataObject>
"""

FUNCTIONAL_OPTION_MDO_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mdclass:FunctionalOption xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass">
  <name>ВестиСведенияДляДекларацийПоАлкогольнойПродукции</name>
  <location>Constant.ВестиСведенияДляДекларацийПоАлкогольнойПродукции</location>
  <content>Document.ext_ЗаявлениеОВыдачеФСМ</content>
  <content>Document.ext_НакладнаяНаВыдачуФСМ</content>
</mdclass:FunctionalOption>
"""

RIGHTS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<Rights xmlns="http://v8.1c.ru/8.2/roles" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="Rights">
  <setForNewObjects>false</setForNewObjects>
  <setForAttributesByDefault>true</setForAttributesByDefault>
  <independentRightsOfChildObjects>false</independentRightsOfChildObjects>
  <object><name>Document.ПриобретениеТоваров</name>
    <right><name>Read</name><value>true</value></right>
    <right><name>Update</name><value>true</value></right>
    <right><name>View</name><value>false</value></right>
  </object>
  <object><name>Catalog.Номенклатура</name>
    <right><name>Read</name><value>true</value></right>
  </object>
</Rights>
"""


# === Enum / FunctionalOption / Rights XML parser tests ===

def test_parse_enum_xml_cf():
    result = parse_enum_xml(ENUM_CF_XML)
    assert result is not None
    assert result["name"] == "СтатусыЗаказов"
    assert result["synonym"] == "Статусы заказов"
    assert len(result["values"]) == 3
    assert result["values"][0]["name"] == "Новый"
    assert result["values"][0]["synonym"] == "Новый"
    assert result["values"][1]["name"] == "ВРаботе"
    assert result["values"][1]["synonym"] == "В работе"
    assert result["values"][2]["name"] == "Закрыт"
    assert result["values"][2]["synonym"] == "Закрыт"


def test_parse_enum_xml_edt():
    result = parse_enum_xml(ENUM_MDO_XML)
    assert result is not None
    assert result["name"] == "ВажностьПроблемы"
    assert len(result["values"]) == 2
    assert result["values"][0]["name"] == "Предупреждение"
    assert result["values"][1]["name"] == "Ошибка"


def test_parse_functional_option_xml_cf():
    result = parse_functional_option_xml(FUNCTIONAL_OPTION_CF_XML)
    assert result is not None
    assert result["name"] == "ИспользоватьСерии"
    assert result["synonym"] == "Использовать серии"
    assert result["location"] == "Constant.ИспользоватьСерии"
    assert len(result["content"]) == 2
    assert "Document.ПриобретениеТоваров" in result["content"]
    assert "Catalog.СерииНоменклатуры" in result["content"]


def test_parse_functional_option_xml_edt():
    result = parse_functional_option_xml(FUNCTIONAL_OPTION_MDO_XML)
    assert result is not None
    assert result["name"] == "ВестиСведенияДляДекларацийПоАлкогольнойПродукции"
    assert result["location"] == "Constant.ВестиСведенияДляДекларацийПоАлкогольнойПродукции"
    assert len(result["content"]) == 2
    assert "Document.ext_ЗаявлениеОВыдачеФСМ" in result["content"]
    assert "Document.ext_НакладнаяНаВыдачуФСМ" in result["content"]


def test_parse_rights_xml():
    result = parse_rights_xml(RIGHTS_XML)
    assert len(result) == 2
    doc = next(r for r in result if r["object"] == "Document.ПриобретениеТоваров")
    assert "Read" in doc["rights"]
    assert "Update" in doc["rights"]
    assert "View" not in doc["rights"]  # value=false excluded
    cat = next(r for r in result if r["object"] == "Catalog.Номенклатура")
    assert cat["rights"] == ["Read"]


def test_parse_rights_xml_filter():
    result = parse_rights_xml(RIGHTS_XML, "ПриобретениеТоваров")
    assert len(result) == 1
    assert result[0]["object"] == "Document.ПриобретениеТоваров"
    assert "Read" in result[0]["rights"]


# === Integration tests for find_enum_values, find_functional_options, find_roles ===

def test_find_enum_values():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        # Add Enum fixture file
        enum_dir = os.path.join(tmpdir, "Enums", "СтатусыЗаказов")
        os.makedirs(enum_dir)
        with open(os.path.join(enum_dir, "СтатусыЗаказов.xml"), "w", encoding="utf-8") as f:
            f.write(ENUM_CF_XML)
        result = bsl["find_enum_values"]("СтатусыЗаказов")
        assert "error" not in result
        assert result["name"] == "СтатусыЗаказов"
        assert len(result["values"]) == 3
        assert "file" in result


def test_find_enum_values_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_enum_values"]("НесуществующееПеречисление")
        assert "error" in result


def test_find_functional_options():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        # Add FunctionalOption fixture file
        fo_dir = os.path.join(tmpdir, "FunctionalOptions")
        os.makedirs(fo_dir)
        with open(os.path.join(fo_dir, "ИспользоватьСерии.xml"), "w", encoding="utf-8") as f:
            f.write(FUNCTIONAL_OPTION_CF_XML)
        result = bsl["find_functional_options"]("ПриобретениеТоваров")
        assert result["object"] == "ПриобретениеТоваров"
        assert len(result["xml_options"]) >= 1
        assert result["xml_options"][0]["name"] == "ИспользоватьСерии"


def test_find_roles():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        # Add Role/Rights fixture file
        role_dir = os.path.join(tmpdir, "Roles", "Менеджер", "Ext")
        os.makedirs(role_dir)
        with open(os.path.join(role_dir, "Rights.xml"), "w", encoding="utf-8") as f:
            f.write(RIGHTS_XML)
        result = bsl["find_roles"]("ПриобретениеТоваров")
        assert result["object"] == "ПриобретениеТоваров"
        assert len(result["roles"]) >= 1
        role = result["roles"][0]
        assert role["role_name"] == "Менеджер"
        assert "Read" in role["rights"]
        assert "file" in role


def test_find_roles_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_roles"]("НесуществующийОбъект")
        assert len(result["roles"]) == 0


def test_parse_cf_event_subscription():
    result = parse_event_subscription_xml(EVENT_SUB_CF_XML)
    assert result is not None
    assert result["name"] == "ЗаписатьВерсиюДокумента"
    assert result["synonym"] == "Записать версию документа"
    assert result["event"] == "BeforeWrite"
    assert result["handler"] == "CommonModule.ВерсионированиеОбъектовСобытия.ЗаписатьВерсиюДокумента"
    assert len(result["source_types"]) == 2
    assert "DocumentObject.АвансовыйОтчет" in result["source_types"]
    assert "DocumentObject.ЗаказКлиента" in result["source_types"]


def test_parse_mdo_event_subscription():
    result = parse_event_subscription_xml(EVENT_SUB_MDO_XML)
    assert result is not None
    assert result["name"] == "тст_ЗаписатьВерсиюДокумента"
    assert result["synonym"] == "Записать версию документа"
    assert result["event"] == "BeforeWrite"
    assert len(result["source_types"]) == 2
    assert "DocumentObject.АвансовыйОтчет" in result["source_types"]


def test_parse_cf_scheduled_job():
    result = parse_scheduled_job_xml(SCHEDULED_JOB_CF_XML)
    assert result is not None
    assert result["name"] == "ЗагрузкаКурсовВалют"
    assert result["synonym"] == "Загрузка курсов валют"
    assert result["method_name"] == "CommonModule.РаботаСКурсамиВалют.ЗагрузитьАктуальныйКурс"
    assert result["use"] is False
    assert result["predefined"] is True
    assert result["restart_on_failure"]["count"] == 10
    assert result["restart_on_failure"]["interval"] == 600


def test_parse_mdo_scheduled_job():
    result = parse_scheduled_job_xml(SCHEDULED_JOB_MDO_XML)
    assert result is not None
    assert result["name"] == "ext_ОтправкаПодтверждения"
    assert result["synonym"] == "Отправка подтверждения поставки"
    assert result["method_name"] == "CommonModule.ext_РегламентныеЗадания.ОтправкаПодтверждения"
    assert result["use"] is True  # EDT default
    assert result["predefined"] is True
    assert result["restart_on_failure"]["count"] == 3


# === Integration tests for new helpers ===

BSL_DOC_WITH_MOVEMENTS = """\
Процедура ОбработкаПроведения(Отказ) Экспорт
    Движения.ТоварыНаСкладах.Записать = Истина;
    Движения.ТоварыНаСкладах.Очистить();
    Движения.РасчетыСПоставщиками.Записать = Истина;
КонецПроцедуры
"""

BSL_DOC_OBJECT_FULL = """\
Процедура ОбработкаЗаполнения(ДанныеЗаполнения) Экспорт
    Если ТипЗнч(ДанныеЗаполнения) = Тип("ДокументСсылка.ЗаказПоставщику") Тогда
        ЗаполнитьНаОсновании(ДанныеЗаполнения);
    ИначеЕсли ТипЗнч(ДанныеЗаполнения) = Тип("СправочникСсылка.ДоговорыКонтрагентов") Тогда
        ЗаполнитьПоДоговору(ДанныеЗаполнения);
    КонецЕсли;
КонецПроцедуры

Процедура ОбработкаПроведения(Отказ) Экспорт
    Движения.ТоварыНаСкладах.Записать = Истина;
    Движения.ТоварыНаСкладах.Очистить();
    Движения.РасчетыСПоставщиками.Записать = Истина;
КонецПроцедуры
"""

BSL_DOC_MANAGER = """\
Процедура ДобавитьКомандыСозданияНаОсновании(КомандыСозданияНаОсновании, Параметры) Экспорт
    Документы.ВозвратТоваров.ДобавитьКомандуСоздатьНаОсновании(КомандыСозданияНаОсновании);
    Документы.СписаниеТоваров.ДобавитьКомандуСоздатьНаОсновании(КомандыСозданияНаОсновании);
КонецПроцедуры

Процедура ДобавитьКомандыПечати(КомандыПечати) Экспорт
    УправлениеПечатью.ДобавитьКомандуПечати(КомандыПечати, "Накладная", НСтр("ru = 'Товарная накладная'"));
    УправлениеПечатью.ДобавитьКомандуПечати(КомандыПечати, "СчетНаОплату", НСтр("ru = 'Счет на оплату'"));
КонецПроцедуры
"""

BSL_DOC_ERP_MANAGER = """\
Процедура ЗарегистрироватьУчетныеМеханизмы(МеханизмыДокумента) Экспорт
    МеханизмыДокумента.Добавить("Взаиморасчеты");
    МеханизмыДокумента.Добавить("Продажи");
    МеханизмыДокумента.Добавить("СебестоимостьИПартионныйУчет");
КонецПроцедуры

Функция АдаптированныйТекстЗапросаДвиженийПоРегистру(ИмяРегистра) Экспорт
    Если ИмяРегистра = "ЗаказыКлиентов" Тогда
        Возврат "";
    ИначеЕсли ИмяРегистра = "РеестрДокументов" Тогда
        Возврат "";
    КонецЕсли;
КонецФункции

Функция ТекстЗапросаТаблицаТовары() Экспорт
    Возврат "";
КонецФункции

Функция ТекстЗапросаТаблицаВидыЗапасов() Экспорт
    Возврат "";
КонецФункции
"""

BSL_DOC_ERP_OBJECT = """\
Процедура ОбработкаПроведения(Отказ, РежимПроведения)
    ПроведениеДокументов.ОбработкаПроведенияДокумента(ЭтотОбъект, Отказ);
КонецПроцедуры
"""


def _make_full_fixture(tmpdir):
    """Create fixture with event subscriptions, scheduled jobs, and document with movements."""
    # BSL modules
    mod_dir = os.path.join(tmpdir, "CommonModules", "МойМодуль", "Ext")
    os.makedirs(mod_dir)
    with open(os.path.join(mod_dir, "Module.bsl"), "w", encoding="utf-8") as f:
        f.write(BSL_CODE)

    # Document with register movements + ОбработкаЗаполнения
    doc_dir = os.path.join(tmpdir, "Documents", "ПриобретениеТоваров", "Ext")
    os.makedirs(doc_dir)
    with open(os.path.join(doc_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
        f.write(BSL_DOC_OBJECT_FULL)

    # Add ManagerModule
    with open(os.path.join(doc_dir, "ManagerModule.bsl"), "w", encoding="utf-8") as f:
        f.write(BSL_DOC_MANAGER)

    # EventSubscription
    sub_dir = os.path.join(tmpdir, "EventSubscriptions")
    os.makedirs(sub_dir)
    with open(os.path.join(sub_dir, "ЗаписатьВерсию.xml"), "w", encoding="utf-8") as f:
        f.write(EVENT_SUB_CF_XML)

    # ScheduledJob
    job_dir = os.path.join(tmpdir, "ScheduledJobs")
    os.makedirs(job_dir)
    with open(os.path.join(job_dir, "ЗагрузкаКурсов.xml"), "w", encoding="utf-8") as f:
        f.write(SCHEDULED_JOB_CF_XML)

    # Configuration.xml
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
    return bsl, helpers


def test_find_event_subscriptions_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_event_subscriptions"]()
        assert len(result) >= 1
        sub = result[0]
        assert sub["name"] == "ЗаписатьВерсиюДокумента"
        assert sub["event"] == "BeforeWrite"
        assert sub["handler_module"] == "ВерсионированиеОбъектовСобытия"
        assert sub["handler_procedure"] == "ЗаписатьВерсиюДокумента"
        # Without filter, source_types should be excluded
        assert "source_types" not in sub


def test_find_event_subscriptions_filtered():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_event_subscriptions"]("АвансовыйОтчет")
        assert len(result) >= 1
        # With filter, source_types should be included
        assert "source_types" in result[0]


def test_find_event_subscriptions_no_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_event_subscriptions"]("НесуществующийДокумент")
        assert len(result) == 0


def test_find_scheduled_jobs_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_scheduled_jobs"]()
        assert len(result) >= 1
        job = result[0]
        assert job["name"] == "ЗагрузкаКурсовВалют"
        assert job["handler_module"] == "РаботаСКурсамиВалют"
        assert job["handler_procedure"] == "ЗагрузитьАктуальныйКурс"
        assert job["use"] is False


def test_find_scheduled_jobs_filtered():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_scheduled_jobs"]("Курс")
        assert len(result) >= 1
        assert result[0]["name"] == "ЗагрузкаКурсовВалют"


def test_find_scheduled_jobs_no_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_scheduled_jobs"]("НесуществующееЗадание")
        assert len(result) == 0


def test_find_register_movements():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_register_movements"]("ПриобретениеТоваров")
        assert result["document"] == "ПриобретениеТоваров"
        assert len(result["code_registers"]) == 2
        reg_names = [r["name"] for r in result["code_registers"]]
        assert "ТоварыНаСкладах" in reg_names
        assert "РасчетыСПоставщиками" in reg_names
        # ТоварыНаСкладах appears on 2 lines
        товары = next(r for r in result["code_registers"] if r["name"] == "ТоварыНаСкладах")
        assert len(товары["lines"]) == 2


def test_find_register_movements_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_register_movements"]("НесуществующийДок")
        assert "error" in result


def test_find_register_writers():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_register_writers"]("ТоварыНаСкладах")
        assert result["register"] == "ТоварыНаСкладах"
        assert result["total_writers"] >= 1
        writers = result["writers"]
        assert any(w["document"] == "ПриобретениеТоваров" for w in writers)


def test_find_register_writers_no_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_register_writers"]("НесуществующийРегистр")
        assert result["total_writers"] == 0


def test_analyze_document_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["analyze_document_flow"]("ПриобретениеТоваров")
        assert "metadata" in result
        assert "event_subscriptions" in result
        assert "register_movements" in result
        assert "related_scheduled_jobs" in result
        # Should find register movements
        regs = result["register_movements"].get("code_registers", [])
        assert len(regs) >= 1


def test_help_subscriptions():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("подписки")
        assert "find_event_subscriptions" in text


def test_help_movements():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("движения")
        assert "find_register_movements" in text


def test_help_jobs():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("регламентные задания")
        assert "find_scheduled_jobs" in text


def test_help_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("как работает документ")
        assert "analyze_document_flow" in text


# === Task 5: find_based_on_documents ===

def test_find_based_on_documents():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_based_on_documents"]("ПриобретениеТоваров")
        assert len(result["can_create_from_here"]) >= 2
        names = [d["document"] for d in result["can_create_from_here"]]
        assert "ВозвратТоваров" in names
        assert "СписаниеТоваров" in names
        assert len(result["can_be_created_from"]) >= 1
        types = [d["type"] for d in result["can_be_created_from"]]
        assert "ДокументСсылка.ЗаказПоставщику" in types


def test_find_based_on_documents_no_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_based_on_documents"]("НесуществующийДок")
        assert len(result["can_create_from_here"]) == 0
        assert len(result["can_be_created_from"]) == 0


# === Task 6: find_print_forms ===

def test_find_print_forms():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_print_forms"]("ПриобретениеТоваров")
        assert len(result["print_forms"]) >= 2
        names = [p["name"] for p in result["print_forms"]]
        assert "Накладная" in names
        assert "СчетНаОплату" in names
        # Check presentation
        nakl = next(p for p in result["print_forms"] if p["name"] == "Накладная")
        assert nakl["presentation"] == "Товарная накладная"


def test_find_print_forms_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        result = bsl["find_print_forms"]("НесуществующийДок")
        assert len(result["print_forms"]) == 0


# === Task 7: find_register_movements ERP framework fallback ===

def test_find_register_movements_erp_framework():
    with tempfile.TemporaryDirectory() as tmpdir:
        doc_dir = os.path.join(tmpdir, "Documents", "РеализацияТоваров", "Ext")
        os.makedirs(doc_dir)
        with open(os.path.join(doc_dir, "ObjectModule.bsl"), "w", encoding="utf-8") as f:
            f.write(BSL_DOC_ERP_OBJECT)
        with open(os.path.join(doc_dir, "ManagerModule.bsl"), "w", encoding="utf-8") as f:
            f.write(BSL_DOC_ERP_MANAGER)
        with open(os.path.join(tmpdir, "Configuration.xml"), "w") as f:
            f.write("<Configuration/>")

        from rlm_tools_bsl.helpers import make_helpers
        from rlm_tools_bsl.format_detector import detect_format
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

        result = bsl["find_register_movements"]("РеализацияТоваров")
        assert len(result["code_registers"]) == 0  # No direct Движения.X
        assert len(result["erp_mechanisms"]) == 3
        assert "Взаиморасчеты" in result["erp_mechanisms"]
        assert "Продажи" in result["erp_mechanisms"]
        assert len(result["manager_tables"]) >= 2
        assert "Товары" in result["manager_tables"]
        assert "ВидыЗапасов" in result["manager_tables"]
        assert "ЗаказыКлиентов" in result["adapted_registers"]
        assert "РеестрДокументов" in result["adapted_registers"]


# === Task 8: help recipes for new helpers ===

def test_help_based_on():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("ввод на основании")
        assert "find_based_on_documents" in text


def test_help_print_forms():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("печатные формы")
        assert "find_print_forms" in text


def test_help_enum():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("значения перечисления")
        assert "find_enum_values" in text


def test_help_roles():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("права доступа")
        assert "find_roles" in text


def test_help_functional_options():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        text = bsl["help"]("functional options")
        assert "find_functional_options" in text


# === Auto-strip metadata type prefix ===

def test_strip_meta_prefix_find_module(bsl_env):
    # With prefix
    r1 = bsl_env.bsl["find_module"]("Документ.МойМодуль")
    # Without prefix
    r2 = bsl_env.bsl["find_module"]("МойМодуль")
    assert len(r1) == len(r2)
    assert r1[0]["object_name"] == r2[0]["object_name"]


def test_strip_meta_prefix_find_register_movements():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_full_fixture(tmpdir)
        r1 = bsl["find_register_movements"]("Документ.ПриобретениеТоваров")
        r2 = bsl["find_register_movements"]("ПриобретениеТоваров")
        assert r1["document"] == r2["document"]
        assert len(r1["code_registers"]) == len(r2["code_registers"])


def test_strip_meta_prefix_find_enum_values():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        # Create enum fixture
        enum_dir = os.path.join(tmpdir, "Enums", "СтатусыЗаказов")
        os.makedirs(enum_dir)
        with open(os.path.join(enum_dir, "СтатусыЗаказов.xml"), "w", encoding="utf-8") as f:
            f.write(ENUM_CF_XML)
        r1 = bsl["find_enum_values"]("Перечисление.СтатусыЗаказов")
        r2 = bsl["find_enum_values"]("СтатусыЗаказов")
        assert r1["name"] == r2["name"]


# === source_count=0 subscriptions (catch-all) ===

EVENT_SUB_CATCHALL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"
    xmlns:v8="http://v8.1c.ru/8.1/data/core"
    xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config">
<EventSubscription uuid="ca1f402d-0000-0000-0000-000000000002">
  <Properties>
    <Name>ктнПередЗаписьюДокумента</Name>
    <Synonym>
      <v8:item><v8:lang>ru</v8:lang><v8:content>Перед записью документа</v8:content></v8:item>
    </Synonym>
    <Source/>
    <Event>BeforeWrite</Event>
    <Handler>CommonModule.ктнПроведение.ПередЗаписьюДокумента</Handler>
  </Properties>
</EventSubscription>
</MetaDataObject>
"""


def test_find_event_subscriptions_catchall():
    """Subscriptions with source_count=0 should match any object name filter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create base fixture
        mod_dir = os.path.join(tmpdir, "CommonModules", "МойМодуль", "Ext")
        os.makedirs(mod_dir)
        with open(os.path.join(mod_dir, "Module.bsl"), "w", encoding="utf-8") as f:
            f.write(BSL_CODE)
        sub_dir = os.path.join(tmpdir, "EventSubscriptions")
        os.makedirs(sub_dir)
        # Normal subscription with specific sources
        with open(os.path.join(sub_dir, "ЗаписатьВерсию.xml"), "w", encoding="utf-8") as f:
            f.write(EVENT_SUB_CF_XML)
        # Catch-all subscription (empty Source)
        with open(os.path.join(sub_dir, "ктнПередЗаписью.xml"), "w", encoding="utf-8") as f:
            f.write(EVENT_SUB_CATCHALL_XML)
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

        # Filter by a specific object — should return BOTH the matching sub AND the catch-all
        result = bsl["find_event_subscriptions"]("АвансовыйОтчет")
        names = [s["name"] for s in result]
        assert "ЗаписатьВерсиюДокумента" in names  # has АвансовыйОтчет in sources
        assert "ктнПередЗаписьюДокумента" in names  # catch-all, source_count=0

        # Filter by non-existing object — should still return catch-all
        result2 = bsl["find_event_subscriptions"]("НесуществующийОбъект")
        names2 = [s["name"] for s in result2]
        assert "ктнПередЗаписьюДокумента" in names2
        assert "ЗаписатьВерсиюДокумента" not in names2


# === custom_only parameter ===

def test_find_event_subscriptions_custom_only():
    """custom_only=True should filter by auto-detected prefixes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create fixture with both standard and custom subscriptions
        mod_dir = os.path.join(tmpdir, "CommonModules", "ктнМодуль", "Ext")
        os.makedirs(mod_dir)
        with open(os.path.join(mod_dir, "Module.bsl"), "w", encoding="utf-8") as f:
            f.write(BSL_CODE)
        # Need 3+ objects with "ктн" prefix for auto-detect threshold
        for name in ["ктнМодуль2", "ктнМодуль3"]:
            d = os.path.join(tmpdir, "CommonModules", name, "Ext")
            os.makedirs(d)
            with open(os.path.join(d, "Module.bsl"), "w", encoding="utf-8") as f:
                f.write("// stub\n")

        sub_dir = os.path.join(tmpdir, "EventSubscriptions")
        os.makedirs(sub_dir)
        with open(os.path.join(sub_dir, "ЗаписатьВерсию.xml"), "w", encoding="utf-8") as f:
            f.write(EVENT_SUB_CF_XML)
        with open(os.path.join(sub_dir, "ктнПередЗаписью.xml"), "w", encoding="utf-8") as f:
            f.write(EVENT_SUB_CATCHALL_XML)
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

        # Without custom_only — should return both
        all_subs = bsl["find_event_subscriptions"]()
        assert len(all_subs) == 2

        # With custom_only — should return only "ктн" prefixed
        custom_subs = bsl["find_event_subscriptions"]("", custom_only=True)
        assert len(custom_subs) == 1
        assert custom_subs[0]["name"] == "ктнПередЗаписьюДокумента"


# ── extract_queries tests ─────────────────────────────────────

def test_extract_queries_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        mod_dir = os.path.join(tmpdir, "Documents", "ТестовыйДокумент", "Ext")
        os.makedirs(mod_dir, exist_ok=True)
        bsl_path = os.path.join(mod_dir, "ObjectModule.bsl")
        with open(bsl_path, "w", encoding="utf-8-sig") as f:
            f.write(
                'Процедура ОбработкаПроведения(Отказ)\n'
                '    Запрос = Новый Запрос;\n'
                '    Запрос.Текст = "ВЫБРАТЬ\n'
                '    |    Т.Ссылка\n'
                '    |ИЗ\n'
                '    |    РегистрНакопления.ТоварыНаСкладах КАК Т\n'
                '    |    СОЕДИНЕНИЕ Справочник.Номенклатура КАК Н\n'
                '    |    ПО Т.Номенклатура = Н.Ссылка";\n'
                'КонецПроцедуры\n'
            )
        rel_path = os.path.relpath(bsl_path, tmpdir).replace("\\", "/")
        queries = bsl["extract_queries"](rel_path)
        assert len(queries) >= 1
        q = queries[0]
        assert q["procedure"] == "ОбработкаПроведения"
        assert "РегистрНакопления.ТоварыНаСкладах" in q["tables"]
        assert "Справочник.Номенклатура" in q["tables"]
        assert "text_preview" in q


def test_extract_queries_no_queries():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        modules = bsl["find_module"]("МойМодуль")
        assert modules
        queries = bsl["extract_queries"](modules[0]["path"])
        assert queries == []


# ── code_metrics tests ────────────────────────────────────────

def test_code_metrics_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        bsl, _ = _make_bsl_fixture(tmpdir)
        mod_dir = os.path.join(tmpdir, "CommonModules", "МетрикиТест", "Ext")
        os.makedirs(mod_dir, exist_ok=True)
        bsl_path = os.path.join(mod_dir, "Module.bsl")
        with open(bsl_path, "w", encoding="utf-8-sig") as f:
            f.write(
                '// Комментарий\n'
                '\n'
                'Процедура Тест1() Экспорт\n'
                '    Если Истина Тогда\n'
                '        Для Каждого Элемент Из Список Цикл\n'
                '            Сообщить(Элемент);\n'
                '        КонецЦикла;\n'
                '    КонецЕсли;\n'
                'КонецПроцедуры\n'
                '\n'
                'Функция Тест2()\n'
                '    Возврат 1;\n'
                'КонецФункции\n'
            )
        rel_path = os.path.relpath(bsl_path, tmpdir).replace("\\", "/")
        m = bsl["code_metrics"](rel_path)
        assert m["total_lines"] == 13
        assert m["comment_lines"] == 1
        assert m["empty_lines"] == 2
        assert m["code_lines"] == 10
        assert m["procedures_count"] == 2
        assert m["exports_count"] == 1
        assert m["max_nesting"] == 2  # Если + Для
        assert m["avg_proc_size"] > 0
