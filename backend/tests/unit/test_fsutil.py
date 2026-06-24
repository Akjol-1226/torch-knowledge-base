import json

import pytest

from app.core.fsutil import safe_name, write_json_atomic, write_text_atomic


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "etcpasswd"),
        ("..\\..\\win", "win"),
        ("kb-1_火炬", "kb-1_火炬"),      # 中文是字母数字（isalnum）→ 保留（kb 名可中文）；仅去路径分隔符
        ("a.b..c", "abc"),               # 点被剔除（防 .. 穿越）
        ("a/b/c", "abc"),
        ("", "_"),                        # 空 → default
        ("///", "_"),
    ],
)
def test_safe_name_strips_traversal(raw, expected):
    assert safe_name(raw) == expected


def test_safe_name_custom_default_and_maxlen():
    assert safe_name("", default="default") == "default"
    assert safe_name("a" * 100, maxlen=10) == "a" * 10


def test_write_text_atomic_roundtrip_and_no_tmp_left(tmp_path):
    p = tmp_path / "sub" / "f.txt"
    write_text_atomic(p, "hello 火炬")
    assert p.read_text(encoding="utf-8") == "hello 火炬"
    # 不留临时文件
    assert [x.name for x in (tmp_path / "sub").iterdir()] == ["f.txt"]


def test_write_json_atomic_roundtrip(tmp_path):
    p = tmp_path / "d.json"
    write_json_atomic(p, {"k": [1, 2, "火炬"]}, indent=2)
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": [1, 2, "火炬"]}


def test_write_text_atomic_overwrites(tmp_path):
    p = tmp_path / "f.txt"
    write_text_atomic(p, "old")
    write_text_atomic(p, "new")
    assert p.read_text(encoding="utf-8") == "new"
