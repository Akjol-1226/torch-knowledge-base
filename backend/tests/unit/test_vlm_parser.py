from app.core.vlm.parser import parse_notsure_segments


def test_parse_empty_text() -> None:
    assert parse_notsure_segments("plain text") == []


def test_parse_single_notsure() -> None:
    text = "before <notsure>uncertain part</notsure> after"
    segments = parse_notsure_segments(text)
    assert len(segments) == 1
    assert segments[0].text == "uncertain part"
    assert text[segments[0].start : segments[0].end] == "<notsure>uncertain part</notsure>"


def test_parse_multiple_notsure() -> None:
    text = "<notsure>a</notsure> middle <notsure>b</notsure>"
    segments = parse_notsure_segments(text)
    assert [s.text for s in segments] == ["a", "b"]


def test_parse_nested_notsure_outermost_wins() -> None:
    # 嵌套时取最外层（栈式扫描）
    text = "<notsure>outer <notsure>inner</notsure> rest</notsure>"
    segments = parse_notsure_segments(text)
    assert len(segments) == 1
    assert "outer" in segments[0].text and "rest" in segments[0].text


def test_parse_multiline_notsure() -> None:
    text = "<notsure>line1\nline2</notsure>"
    segments = parse_notsure_segments(text)
    assert segments[0].text == "line1\nline2"
