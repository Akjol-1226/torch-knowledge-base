"""节点级向量索引：内存 numpy 矩阵（~700 行规模，不引入 faiss/向量库）。

落盘 indexes/embeddings.npy（N×D，L2 归一化）+ indexes/vec_meta.json
（provider/model 签名、ids、内容 hash）。
重建时按内容 hash 复用未变节点的旧向量，避免每次全量重调 Proxy。
provider/model 签名不一致（向量空间变了）→ 全量重编码。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from app.core.fsutil import write_json_atomic
from app.core.logging import get_logger

from .embed import EmbeddingClient, compose_text

log = get_logger("retrieval.vector")

_EMB_FILE = "embeddings.npy"
_META_FILE = "vec_meta.json"


def _hash(text: str) -> str:
    # 仅用于内容指纹去重/缓存，非安全用途
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _np_save_atomic(path: Path, arr: np.ndarray) -> None:
    """原子写 .npy：写同目录临时文件再 os.replace，避免读到写一半的 npy。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".npy")
    os.close(fd)
    try:
        np.save(tmp, arr, allow_pickle=False)  # tmp 已以 .npy 结尾，np.save 不再追加后缀
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class VectorIndex:
    def __init__(self, ids: list[str], embeddings: np.ndarray, signature: str, hashes: list[str]):
        self.ids = ids
        self.embeddings = embeddings  # (N, D) float32, L2 归一化
        self.signature = signature
        self.hashes = hashes

    # ---- 构建 ----
    @classmethod
    def build(
        cls,
        records: list[dict],
        embed: EmbeddingClient,
        max_chars: int,
        old: VectorIndex | None = None,
    ) -> VectorIndex:
        ids = [r["node_id_full"] for r in records]
        texts = [compose_text(r, max_chars) for r in records]
        hashes = [_hash(t) for t in texts]

        # 缓存复用：签名一致时，按 hash 命中旧向量，只编码缺失项
        reuse: dict[str, np.ndarray] = {}
        if old is not None and old.signature == embed.signature:
            for h, row in zip(old.hashes, old.embeddings, strict=False):
                reuse.setdefault(h, row)

        miss_idx = [i for i, h in enumerate(hashes) if h not in reuse]
        if miss_idx:
            new_vecs = embed.embed([texts[i] for i in miss_idx])
            for j, i in enumerate(miss_idx):
                reuse[hashes[i]] = new_vecs[j]
        log.info(
            "vector_index_built",
            total=len(ids), embedded=len(miss_idx), reused=len(ids) - len(miss_idx),
        )

        embeddings = (
            np.vstack([reuse[h] for h in hashes]).astype(np.float32)
            if ids else np.zeros((0, 0), np.float32)
        )
        return cls(ids, embeddings, embed.signature, hashes)

    # ---- 持久化 ----
    def save(self, dir_path) -> None:
        d = Path(dir_path)
        d.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再 os.replace，避免并发 TreeStore 重载读到写一半的 npy/json
        _np_save_atomic(d / _EMB_FILE, self.embeddings)
        write_json_atomic(
            d / _META_FILE,
            {"signature": self.signature, "ids": self.ids, "hashes": self.hashes},
        )

    @classmethod
    def load(cls, dir_path) -> VectorIndex | None:
        d = Path(dir_path)
        emb_p, meta_p = d / _EMB_FILE, d / _META_FILE
        if not (emb_p.exists() and meta_p.exists()):
            return None
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            embeddings = np.load(emb_p)
            return cls(meta["ids"], embeddings, meta["signature"], meta.get("hashes", []))
        except Exception:
            log.exception("vector_index_load_failed")
            return None

    # ---- 检索 ----
    def search(self, qvec: np.ndarray, top_n: int, threshold: float) -> list[dict]:
        """qvec: (D,) L2 归一化。返回 [{node_id_full, sim}]，按 sim 降序、过滤 sim<threshold。"""
        if self.embeddings.size == 0 or not self.ids:
            return []
        # 维度不匹配（换过 embedding 模型但签名巧合相同）→ 不崩，返回空让上层退回 BM25
        if qvec.shape[0] != self.embeddings.shape[1]:
            return []
        sims = self.embeddings @ qvec  # 余弦（两侧已归一化）
        n = min(top_n, len(self.ids))
        # 取 top_n（无序）再精排，避免对全量排序
        idx = np.argpartition(-sims, n - 1)[:n]
        idx = idx[np.argsort(-sims[idx])]
        return [
            {"node_id_full": self.ids[int(i)], "sim": float(sims[int(i)])}
            for i in idx
            if sims[int(i)] >= threshold
        ]
