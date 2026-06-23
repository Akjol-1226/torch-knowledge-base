from app.core.docparse.pdf_extractor import _detect_toc_page, level_for_number


def test_detect_toc_page_by_keyword():
    assert _detect_toc_page("目录\n1 概述 ......... 1\n1.1 背景 ...... 2") is True


def test_detect_toc_page_by_dotlines():
    text = "\n".join([
        "1 项目概述 .......... 1",
        "1.1 建设背景 ........ 2",
        "1.1.1 数据来源 ...... 3",
        "2 系统架构 .......... 5",
        "2.1 文件解析 ........ 6",
    ])
    assert _detect_toc_page(text) is True


def test_detect_toc_page_normal_content():
    assert _detect_toc_page("本项目建设背景如下：\n1. 市场需求分析\n2. 政策支持") is False


def test_level_for_number_single():
    assert level_for_number("1") == 2
    assert level_for_number("12") == 2


def test_level_for_number_double():
    assert level_for_number("1.1") == 3
    assert level_for_number("2.3") == 3


def test_level_for_number_triple():
    assert level_for_number("1.1.1") == 4


def test_level_for_number_deep():
    assert level_for_number("1.1.1.1.1") == 6  # capped at 6
