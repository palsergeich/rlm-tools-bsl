import os
import tempfile

from rlm_tools_bsl.format_detector import (
    BslFileInfo,
    FormatInfo,
    METADATA_CATEGORIES,
    MODULE_TYPE_MAP,
    SourceFormat,
    detect_format,
    parse_bsl_path,
)


# --- detect_format ---

def test_detect_cf_format():
    """CF format: Configuration.xml + /Ext/ directories with .bsl files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create CF-style structure
        os.makedirs(os.path.join(tmpdir, "CommonModules", "MyModule", "Ext"))
        with open(os.path.join(tmpdir, "CommonModules", "MyModule", "Ext", "Module.bsl"), "w") as f:
            f.write("// code")
        with open(os.path.join(tmpdir, "Configuration.xml"), "w") as f:
            f.write("<Configuration/>")

        info = detect_format(tmpdir)
        assert info.primary_format == SourceFormat.CF
        assert info.has_configuration_xml is True
        assert info.bsl_file_count >= 1
        assert "CommonModules" in info.metadata_categories_found
        assert info.format_label == "cf"


def test_detect_edt_format():
    """EDT format: .mdo files, no /Ext/."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "CommonModules", "MyModule"))
        with open(os.path.join(tmpdir, "CommonModules", "MyModule", "Module.bsl"), "w") as f:
            f.write("// code")
        with open(os.path.join(tmpdir, "CommonModules", "MyModule", "MyModule.mdo"), "w") as f:
            f.write("<mdo/>")

        info = detect_format(tmpdir)
        assert info.primary_format == SourceFormat.EDT
        assert info.bsl_file_count >= 1
        assert "CommonModules" in info.metadata_categories_found
        assert info.format_label == "edt"


def test_detect_unknown_format():
    """Unknown: just .bsl files without CF/EDT markers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "script.bsl"), "w") as f:
            f.write("// code")

        info = detect_format(tmpdir)
        assert info.primary_format == SourceFormat.UNKNOWN
        assert info.bsl_file_count >= 1
        assert info.format_label == "unknown"


def test_detect_empty_directory():
    """Empty directory: UNKNOWN with 0 files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        info = detect_format(tmpdir)
        assert info.primary_format == SourceFormat.UNKNOWN
        assert info.bsl_file_count == 0


# --- parse_bsl_path ---

def test_parse_cf_common_module():
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "CommonModules", "MyModule", "Ext", "Module.bsl"),
            base,
        )
        assert result.category == "CommonModules"
        assert result.object_name == "MyModule"
        assert result.module_type == "Module"
        assert result.is_form_module is False
        assert result.form_name is None
        assert result.command_name is None


def test_parse_cf_document_object_module():
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "Documents", "АвансовыйОтчет", "Ext", "ObjectModule.bsl"),
            base,
        )
        assert result.category == "Documents"
        assert result.object_name == "АвансовыйОтчет"
        assert result.module_type == "ObjectModule"
        assert result.is_form_module is False


def test_parse_cf_form_module():
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "Documents", "АвансовыйОтчет", "Forms", "ФормаДокумента", "Ext", "Form", "Module.bsl"),
            base,
        )
        assert result.category == "Documents"
        assert result.object_name == "АвансовыйОтчет"
        assert result.module_type == "Module"
        assert result.form_name == "ФормаДокумента"
        assert result.is_form_module is True


def test_parse_cf_command_module():
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "Catalogs", "Номенклатура", "Commands", "Print", "Ext", "CommandModule.bsl"),
            base,
        )
        assert result.category == "Catalogs"
        assert result.object_name == "Номенклатура"
        assert result.command_name == "Print"
        assert result.module_type == "CommandModule"


def test_parse_edt_common_module():
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "CommonModules", "тст_Интеграция", "Module.bsl"),
            base,
        )
        assert result.category == "CommonModules"
        assert result.object_name == "тст_Интеграция"
        assert result.module_type == "Module"
        assert result.is_form_module is False


def test_parse_edt_form_module():
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "Catalogs", "тст_ВходящиеСообщения", "Forms", "ФормаСписка", "Module.bsl"),
            base,
        )
        assert result.category == "Catalogs"
        assert result.object_name == "тст_ВходящиеСообщения"
        assert result.form_name == "ФормаСписка"
        assert result.is_form_module is True


def test_parse_flat_path():
    """File without standard metadata structure."""
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "scripts", "myfile.bsl"),
            base,
        )
        assert result.category is None
        assert result.object_name is None
        assert result.is_form_module is False
        assert "myfile.bsl" in result.relative_path


def test_parse_register_module():
    with tempfile.TemporaryDirectory() as base:
        result = parse_bsl_path(
            os.path.join(base, "AccumulationRegisters", "ТоварыНаСкладах", "Ext", "RecordSetModule.bsl"),
            base,
        )
        assert result.category == "AccumulationRegisters"
        assert result.object_name == "ТоварыНаСкладах"
        assert result.module_type == "RecordSetModule"


# --- Constants ---

def test_metadata_categories_is_frozenset():
    assert isinstance(METADATA_CATEGORIES, frozenset)
    assert "CommonModules" in METADATA_CATEGORIES
    assert "Documents" in METADATA_CATEGORIES
    assert len(METADATA_CATEGORIES) >= 20


def test_module_type_map_completeness():
    assert "Module.bsl" in MODULE_TYPE_MAP
    assert "ObjectModule.bsl" in MODULE_TYPE_MAP
    assert "ManagerModule.bsl" in MODULE_TYPE_MAP
    assert MODULE_TYPE_MAP["Module.bsl"] == "Module"
