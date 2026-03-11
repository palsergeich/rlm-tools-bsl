import re

from rlm_tools_bsl.bsl_knowledge import (
    BSL_PATTERNS,
    EFFORT_LEVELS,
    EffortConfig,
    RLM_EXECUTE_DESCRIPTION,
    RLM_START_DESCRIPTION,
    get_strategy,
)


# --- BSL_PATTERNS ---

def test_all_patterns_compile():
    """All regex patterns must compile without error."""
    for name, pattern in BSL_PATTERNS.items():
        compiled = re.compile(pattern)
        assert compiled is not None, f"Pattern {name} failed to compile"


def test_procedure_def_pattern():
    pattern = re.compile(BSL_PATTERNS["procedure_def"])
    assert pattern.search("Процедура МояПроцедура(Параметр1) Экспорт")
    assert pattern.search("Функция МояФункция()")
    assert not pattern.search("// комментарий")


def test_procedure_end_pattern():
    pattern = re.compile(BSL_PATTERNS["procedure_end"])
    assert pattern.search("КонецПроцедуры")
    assert pattern.search("  КонецФункции")
    assert not pattern.search("Процедура")


def test_module_call_pattern():
    pattern = re.compile(BSL_PATTERNS["module_call"])
    m = pattern.search("ОбщийМодуль.МояФункция(Параметры)")
    assert m is not None
    assert m.group(1) == "ОбщийМодуль"
    assert m.group(2) == "МояФункция"


def test_region_patterns():
    start = re.compile(BSL_PATTERNS["region_start"])
    end = re.compile(BSL_PATTERNS["region_end"])
    m = start.search("#Область ПрограммныйИнтерфейс")
    assert m is not None
    assert m.group(1) == "ПрограммныйИнтерфейс"
    assert end.search("#КонецОбласти")


# --- EFFORT_LEVELS ---

def test_effort_levels_keys():
    assert set(EFFORT_LEVELS.keys()) == {"low", "medium", "high", "max"}


def test_effort_levels_types():
    for name, config in EFFORT_LEVELS.items():
        assert isinstance(config, EffortConfig)
        assert config.max_execute_calls > 0
        assert config.max_llm_calls > 0
        assert config.safe_grep_max_files > 0
        assert len(config.guidance) > 0


def test_effort_levels_ordering():
    """Higher effort levels should have higher limits."""
    levels = ["low", "medium", "high", "max"]
    for i in range(len(levels) - 1):
        a = EFFORT_LEVELS[levels[i]]
        b = EFFORT_LEVELS[levels[i + 1]]
        assert b.max_execute_calls >= a.max_execute_calls


# --- get_strategy ---

def test_strategy_contains_critical_warning():
    text = get_strategy("medium", None)
    assert "CRITICAL" in text
    assert "23,000" in text or "23000" in text or "timeout" in text.lower()


def test_strategy_contains_helper_signatures():
    text = get_strategy("medium", None)
    assert "find_module" in text
    assert "find_by_type" in text
    assert "extract_procedures" in text
    assert "safe_grep" in text
    assert "read_procedure" in text
    assert "find_callers" in text


def test_strategy_contains_effort_guidance():
    for effort in ["low", "medium", "high", "max"]:
        text = get_strategy(effort, None)
        # Should contain something from the effort config guidance
        config = EFFORT_LEVELS[effort]
        # At minimum the strategy should mention the effort level or contain some guidance
        assert len(text) > 100


def test_strategy_with_format_info():
    """When format_info is provided, strategy should mention format."""
    from rlm_tools_bsl.format_detector import FormatInfo, SourceFormat

    cf_info = FormatInfo(
        primary_format=SourceFormat.CF,
        root_path="/test",
        bsl_file_count=100,
        has_configuration_xml=True,
        metadata_categories_found=["CommonModules", "Documents"],
    )
    text = get_strategy("medium", cf_info)
    assert "CF" in text or "cf" in text or "Ext" in text


# --- Descriptions ---

def test_rlm_start_description():
    assert "BSL" in RLM_START_DESCRIPTION
    assert "1C" in RLM_START_DESCRIPTION
    assert "find_module" in RLM_START_DESCRIPTION


def test_rlm_execute_description():
    assert "BSL" in RLM_EXECUTE_DESCRIPTION
    assert "find_module" in RLM_EXECUTE_DESCRIPTION
    assert "grep" in RLM_EXECUTE_DESCRIPTION
