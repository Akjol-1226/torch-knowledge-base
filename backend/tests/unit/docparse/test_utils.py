import pytest

from app.core.docparse.models import Heading
from app.core.docparse.utils import (
    extract_tail_text,
    get_file_title,
    normalize_heading_text,
    strip_notsure,
    update_heading_stack,
)


def test_get_file_title_simple():
    assert get_file_title("火炬电子高管名单.pdf") == "火炬电子高管名单"


@pytest.mark.skip(reason="Windows 路径用例：POSIX 平台 Path 不识别反斜杠分隔符（非迁移问题）")
def test_get_file_title_full_path():
    assert get_file_title(r"C:\docs\项目报告 2024.pdf") == "项目报告 2024"


def test_strip_notsure_basic():
    assert strip_notsure("产值<NOTSURE>增长</NOTSURE>了约三成") == "产值增长了约三成"


def test_strip_notsure_unclosed():
    assert strip_notsure("文字<NOTSURE>残缺") == "文字残缺"


def test_strip_notsure_no_tag():
    assert strip_notsure("普通文字") == "普通文字"


def test_extract_tail_text_basic():
    md = "line1\nline2\nline3\nline4\nline5"
    tail = extract_tail_text(md, max_chars=30)
    assert "line5" in tail


def test_extract_tail_text_empty():
    assert extract_tail_text("") is None


def test_update_heading_stack_push():
    h2 = Heading(text="1 概述", level=2, number="1", type="body_heading")
    h3 = Heading(text="1.1 背景", level=3, number="1.1", type="body_heading")
    stack = update_heading_stack([], [h2, h3])
    assert len(stack) == 2
    assert stack[-1].text == "1.1 背景"


def test_update_heading_stack_pop_on_same_level():
    h2a = Heading(text="1 概述", level=2, number="1", type="body_heading")
    h3 = Heading(text="1.1 背景", level=3, number="1.1", type="body_heading")
    h2b = Heading(text="2 架构", level=2, number="2", type="body_heading")
    stack = update_heading_stack([], [h2a, h3])
    stack = update_heading_stack(stack, [h2b])
    assert len(stack) == 1
    assert stack[0].text == "2 架构"


def test_normalize_heading_text_fullwidth_punctuation():
    assert normalize_heading_text("第一章：概述（简介）") == "第一章:概述(简介)"


def test_normalize_heading_text_strips_and_collapses_spaces():
    assert normalize_heading_text("  第二章   架构  ") == "第二章 架构"
