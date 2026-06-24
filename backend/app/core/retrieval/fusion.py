"""加权 RRF（倒数排名融合）。

只对进了候选的那一路计分：缺席某一路 → 该路贡献 0（不是负分）。
故弱向量（如长节点稀释向量）排在后面/未召回时，对结果"沉默"而非拖累 BM25。
"""

from __future__ import annotations


def rrf_fuse(
    bm25_ids: list[str],
    vec_ids: list[str],
    *,
    w_bm25: float,
    w_vec: float,
    k: int,
) -> list[tuple[str, float]]:
    """两路按相关度降序的 id 列表 → 融合后按融合分降序的 [(id, score)]。

    score(id) = w_bm25 / (k + rank_bm25) + w_vec / (k + rank_vec)，rank 从 0 计；
    缺席某一路则该项为 0（不是负分）。
    """
    scores: dict[str, float] = {}
    for rank, _id in enumerate(bm25_ids):
        scores[_id] = scores.get(_id, 0.0) + w_bm25 / (k + rank)
    for rank, _id in enumerate(vec_ids):
        scores[_id] = scores.get(_id, 0.0) + w_vec / (k + rank)
    # 按分数降序；同分时保持 BM25 优先（稳定：先按分，再按是否在 bm25 中、再按 id）
    bm25_pos = {i: r for r, i in enumerate(bm25_ids)}
    return sorted(
        scores.items(),
        key=lambda kv: (-kv[1], bm25_pos.get(kv[0], len(bm25_ids)), kv[0]),
    )
