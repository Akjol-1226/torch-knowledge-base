from app.modules.docedit.diff import make_diff_html, text_hash


def test_text_hash_is_body_only_stable_and_prefixed() -> None:
    assert text_hash("hello\nworld") == text_hash("hello\r\nworld\n")
    assert text_hash("hello").startswith("sha256:")


def test_make_diff_html_escapes_user_html() -> None:
    html = make_diff_html("<script>old</script>", "<b>new</b>")

    assert "<script>" not in html
    assert "<b>" not in html
    assert "&lt;" in html
    assert "<del>" in html
    assert "<ins>" in html
