import os
import tempfile

from rlm_tools_bsl.helpers import make_helpers
from rlm_tools_bsl.format_detector import FormatInfo, SourceFormat, detect_format
from rlm_tools_bsl.bsl_helpers import make_bsl_helpers


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
