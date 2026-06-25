import httpx
import pytest

from app.modules.ingest.doc_convert import (
    ConversionError,
    needs_conversion,
    to_pdf,
)


class FakeResp:
    def __init__(self, status_code=200, content=b"%PDF-1.7 ...", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


def _make(tmp_path, name, data=b"x"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_needs_conversion():
    assert needs_conversion("a.docx") is True
    assert needs_conversion("a.xlsx") is True
    assert needs_conversion("a.txt") is True
    assert needs_conversion("a.pdf") is False
    assert needs_conversion("a.png") is False


def test_pdf_passthrough_no_http(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    src = _make(tmp_path, "doc.pdf", b"%PDF-1.7")
    out = to_pdf(src, "doc.pdf")
    assert out == src  # 原样返回
    assert called["n"] == 0  # 没调 Gotenberg


def test_docx_calls_gotenberg_and_writes_pdf(tmp_path, monkeypatch):
    captured = {}

    def fake_post(url, files=None, timeout=None):
        captured["url"] = url
        captured["fname"] = files["files"][0]
        return FakeResp(200, b"%PDF-1.7 converted")

    monkeypatch.setattr(httpx, "post", fake_post)
    src = _make(tmp_path, "report.docx", b"PK\x03\x04fake-docx")
    out = to_pdf(src, "report.docx")
    assert out.suffix == ".pdf"
    assert out.read_bytes().startswith(b"%PDF")
    assert captured["url"].endswith("/forms/libreoffice/convert")
    assert captured["fname"] == "report.docx"  # 扩展名告诉 LibreOffice 源格式
    out.unlink(missing_ok=True)


def test_unsupported_ext_raises(tmp_path):
    src = _make(tmp_path, "a.png")
    with pytest.raises(ConversionError):
        to_pdf(src, "a.png")


def test_gotenberg_non_200_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(503, b"", "service down"))
    src = _make(tmp_path, "a.docx")
    with pytest.raises(ConversionError):
        to_pdf(src, "a.docx")


def test_gotenberg_non_pdf_response_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(200, b"<html>error</html>"))
    src = _make(tmp_path, "a.docx")
    with pytest.raises(ConversionError):
        to_pdf(src, "a.docx")


def test_gotenberg_unreachable_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    src = _make(tmp_path, "a.docx")
    with pytest.raises(ConversionError):
        to_pdf(src, "a.docx")
