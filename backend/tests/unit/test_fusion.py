from app.core.retrieval.fusion import rrf_fuse

W = {"w_bm25": 0.6, "w_vec": 0.4, "k": 60}


def test_node_in_both_lists_outranks_single_list():
    # c 同时出现在两路 → 融合分应高于只在一路的 a/b/d
    fused = rrf_fuse(["a", "b", "c"], ["c", "d"], **W)
    assert fused[0][0] == "c"


def test_absent_in_vector_still_scored_not_penalized():
    # a 只在 BM25 出现 → 仍有分（向量缺席=0 贡献，不是负分）
    fused = dict(rrf_fuse(["a", "b"], ["b"], **W))
    assert fused["a"] > 0
    # b 两路都在，分应更高
    assert fused["b"] > fused["a"]


def test_weights_shift_ranking():
    # 拉高向量权重，纯向量命中的 z 应超过纯 BM25 命中的 a
    low = dict(rrf_fuse(["a"], ["z"], w_bm25=0.9, w_vec=0.1, k=60))
    assert low["a"] > low["z"]
    high = dict(rrf_fuse(["a"], ["z"], w_bm25=0.1, w_vec=0.9, k=60))
    assert high["z"] > high["a"]


def test_empty_vector_falls_back_to_bm25_order():
    fused = rrf_fuse(["a", "b", "c"], [], **W)
    assert [i for i, _ in fused] == ["a", "b", "c"]


def test_all_ids_preserved():
    fused = {i for i, _ in rrf_fuse(["a", "b"], ["b", "c"], **W)}
    assert fused == {"a", "b", "c"}
