import numpy as np

from app.core.retrieval.vector_index import VectorIndex


class FakeEmbed:
    """装钵→dim0，温度→dim1，其余→dim2。归一化后点积即余弦。"""

    signature = "fake|m1"
    calls = 0

    def embed(self, texts):
        FakeEmbed.calls += len(texts)
        rows = []
        for t in texts:
            if "装钵" in t:
                rows.append([1.0, 0.0, 0.0])
            elif "温度" in t:
                rows.append([0.0, 1.0, 0.0])
            else:
                rows.append([0.0, 0.0, 1.0])
        m = np.asarray(rows, np.float32)
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return (m / n).astype(np.float32)


def _recs():
    return [
        {"node_id_full": "d:1", "title": "装钵规范", "summary": "", "text": "重叠堆放"},
        {"node_id_full": "d:2", "title": "温度曲线", "summary": "", "text": "公差"},
        {"node_id_full": "d:3", "title": "其它", "summary": "", "text": "杂项"},
    ]


def test_build_search_tophit(tmp_path):
    vi = VectorIndex.build(_recs(), FakeEmbed(), 6000)
    q = FakeEmbed().embed(["装钵能否重叠"])[0]
    hits = vi.search(q, top_n=3, threshold=0.3)
    assert hits[0]["node_id_full"] == "d:1"
    assert hits[0]["sim"] > 0.99


def test_threshold_filters_low_sim(tmp_path):
    vi = VectorIndex.build(_recs(), FakeEmbed(), 6000)
    q = FakeEmbed().embed(["装钵"])[0]  # 只和 d:1 同向，与 d:2/d:3 正交(sim=0)
    hits = vi.search(q, top_n=3, threshold=0.3)
    assert [h["node_id_full"] for h in hits] == ["d:1"]


def test_save_load_roundtrip(tmp_path):
    VectorIndex.build(_recs(), FakeEmbed(), 6000).save(tmp_path)
    vi = VectorIndex.load(tmp_path)
    assert vi is not None
    assert vi.ids == ["d:1", "d:2", "d:3"]
    assert vi.signature == "fake|m1"


def test_load_missing_returns_none(tmp_path):
    assert VectorIndex.load(tmp_path) is None


def test_search_dim_mismatch_returns_empty(tmp_path):
    # 换过 embedding 模型但签名巧合相同 → 查询向量维度对不上，不崩、返回空让上层退回 BM25
    vi = VectorIndex.build(_recs(), FakeEmbed(), 6000)  # 3 维
    bad_q = np.ones(5, np.float32)  # 维度不匹配
    assert vi.search(bad_q, top_n=3, threshold=0.3) == []


def test_cache_reuses_unchanged_nodes(tmp_path):
    old = VectorIndex.build(_recs(), FakeEmbed(), 6000)
    FakeEmbed.calls = 0
    recs = _recs()
    recs[2]["text"] = "改了正文"  # 只有 d:3 内容变
    VectorIndex.build(recs, FakeEmbed(), 6000, old=old)
    assert FakeEmbed.calls == 1  # 只重编码 d:3


def test_signature_mismatch_forces_full_reembed(tmp_path):
    old = VectorIndex.build(_recs(), FakeEmbed(), 6000)

    class OtherModel(FakeEmbed):
        signature = "fake|m2"

    FakeEmbed.calls = 0
    VectorIndex.build(_recs(), OtherModel(), 6000, old=old)
    assert FakeEmbed.calls == 3  # 向量空间变了，全量重编码
