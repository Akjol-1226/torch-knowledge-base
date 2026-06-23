from app.core.retrieval.snippet import make_snippet


def test_snippet_centers_on_query_terms():
    text = "前言。" * 20 + "回流焊峰值温度为245℃，217℃以上停留60到90秒。" + "结语。" * 20
    s = make_snippet(text, "峰值温度 245", width=20)
    assert "245" in s and "峰值" in s
    assert len(s) <= 60   # 截断到窗口附近，不返回全文

def test_snippet_no_hit_returns_head():
    s = make_snippet("一二三四五六七八九十", "不存在的词", width=4)
    assert s.startswith("一")
