from app.core.docparse.postprocess import fix_numbered_heading_levels
from app.core.docparse.utils import strip_notsure
from app.core.docparse.validators import (
    ValidationReport,
    repair_markdown,
    repair_notsure_tags,
    validate_notsure_tags,
)


def test_strip_notsure_preserves_content():
    assert strip_notsure("产值<NOTSURE>增长</NOTSURE>了三成") == "产值增长了三成"


def test_notsure_in_heading_preserved_after_level_fix():
    inp = "## 1.1 <NOTSURE>建设背景</NOTSURE>"
    out = fix_numbered_heading_levels(inp)
    assert "<NOTSURE>" in out
    assert "</NOTSURE>" in out
    assert out.startswith("###")


def test_validator_detects_unclosed_tag():
    errors = validate_notsure_tags("文字<NOTSURE>未闭合")
    assert any("mismatch" in e for e in errors)


def test_validator_detects_nested_tags():
    errors = validate_notsure_tags("<NOTSURE><NOTSURE>嵌套</NOTSURE></NOTSURE>")
    assert any("Nested" in e for e in errors)


def test_validator_detects_lowercase_variant():
    errors = validate_notsure_tags("<notsure>小写</notsure>")
    assert any("variant" in e.lower() or "NOTSURE" in e for e in errors)


def test_validator_passes_valid():
    errors = validate_notsure_tags("文字<NOTSURE>模糊</NOTSURE>内容")
    assert errors == []


def test_repair_unclosed_notsure():
    out = repair_notsure_tags("文字<NOTSURE>未闭合")
    assert validate_notsure_tags(out) == []
    assert "<NOTSURE>未闭合</NOTSURE>" in out


def test_repair_nested_notsure():
    out = repair_notsure_tags("<NOTSURE><NOTSURE>嵌套</NOTSURE></NOTSURE>")
    assert validate_notsure_tags(out) == []
    assert out == "<NOTSURE>嵌套</NOTSURE>"


def test_repair_markdown_fixes_notsure():
    report = ValidationReport(errors=["NOTSURE tag count mismatch: open=1, close=0"])
    out = repair_markdown("文字<NOTSURE>未闭合", report)
    assert validate_notsure_tags(out) == []
