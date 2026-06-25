import json
import logging
from pathlib import Path

import bm25s

from app.core.fsutil import write_text_atomic

from .tokenize import tokenize

log = logging.getLogger("retrieval.bm25")

TITLE_BOOST = 3
SUMMARY_BOOST = 2
CONTEXT_BOOST = 2  # 文档名 + 上级章节链：帮"X型号焊接检验"匹配到正文没提 X 的检验节点


def _node_tokens(rec: dict) -> list:
    toks = tokenize(rec["title"]) * TITLE_BOOST
    toks += tokenize(rec.get("context", "")) * CONTEXT_BOOST
    toks += tokenize(rec["summary"]) * SUMMARY_BOOST
    toks += tokenize(rec["text"])
    return toks


class BM25Index:
    def __init__(self, retriever, meta: list):
        self._retriever = retriever
        self._meta = meta  # list[{"node_id_full","title"}]，与语料顺序一致

    def search(self, query: str, top_k: int = 5) -> list:
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        k = min(top_k, len(self._meta))
        results, scores = self._retriever.retrieve(
            [q_tokens], k=k, return_as="tuple", show_progress=False
        )
        hits = []
        for idx, score in zip(results[0], scores[0], strict=False):
            m = self._meta[int(idx)]
            hits.append({"node_id_full": m["node_id_full"],
                         "title": m["title"], "score": float(score)})
        return hits

    def save(self, dir_path) -> None:
        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        self._retriever.save(str(dir_path / "bm25"), show_progress=False)
        # meta.json 原子写，避免并发 TreeStore 重载读到写一半的文件
        write_text_atomic(dir_path / "meta.json", json.dumps(self._meta, ensure_ascii=False))

    @classmethod
    def load(cls, dir_path):
        """加载失败（重建中途读到、文件损坏）返回 None → TreeStore 退化为"索引不可用"而非 500。"""
        dir_path = Path(dir_path)
        try:
            retriever = bm25s.BM25.load(str(dir_path / "bm25"), load_corpus=False)
            meta = json.loads((dir_path / "meta.json").read_text(encoding="utf-8"))
            return cls(retriever, meta)
        except Exception:
            log.warning("bm25_index_load_failed", exc_info=True)
            return None


def build_index(records: list) -> BM25Index:
    corpus_tokens = [_node_tokens(r) for r in records]
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens, show_progress=False)
    meta = [{"node_id_full": r["node_id_full"], "title": r["title"]} for r in records]
    return BM25Index(retriever, meta)
