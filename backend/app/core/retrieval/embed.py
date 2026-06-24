"""Embedding 客户端：把文本编码成 L2 归一化向量（点积即余弦）。

默认走团队 LiteLLM Proxy 的 /embeddings（OpenAI 兼容），与对话/建树同一凭证。
provider=local 预留 onnxruntime 本地中文模型兜底（同一接口，调用方无感）——本期未实现。
向量是增强项：取不到 client（无凭证/无模型）时返回 None，检索侧据此退回纯 BM25。
"""

from __future__ import annotations

import threading
from typing import Protocol

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("retrieval.embed")


def compose_text(rec: dict, max_chars: int) -> str:
    """节点 → 编码输入：title + summary + text 顺序拼接后截断。

    summary（LLM 摘要）是语义核心、放前面 → 截断只丢正文尾部，长节点不整条失真。
    """
    parts = [rec.get("title", ""), rec.get("summary", "") or "", rec.get("text", "") or ""]
    return "\n".join(p for p in parts if p)[:max_chars]


class EmbeddingClient(Protocol):
    signature: str  # provider|model，作为向量空间指纹（变了必须重建索引）

    def embed(self, texts: list[str]) -> np.ndarray: ...  # (N, D) float32, 已 L2 归一化


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


class ProxyEmbeddingClient:
    """LiteLLM Proxy /embeddings（OpenAI 兼容）。"""

    def __init__(self, base_url: str, api_key: str, model: str, batch_size: int):
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
        self._model = model
        self._batch = max(1, batch_size)
        self.signature = f"proxy|{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vecs: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            batch = texts[i : i + self._batch]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            # 按 index 排序，不假设 API 返回顺序与输入一致
            vecs.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return _l2_normalize(np.asarray(vecs, dtype=np.float32))


_client: EmbeddingClient | None = None
_client_sig: str | None = None
_client_lock = threading.Lock()  # 工具跑在线程池 → 防并发重复构建 / 撕裂赋值


def get_embed_client() -> EmbeddingClient | None:
    """惰性单例。返回 None 表示向量不可用（无凭证/无模型/provider 不支持）→ 调用方退回 BM25。

    config 变更（base_url/model/provider）时重建，避免用到过期客户端。
    """
    global _client, _client_sig
    if _client_sig == "__injected__":  # 测试注入优先，绕过 config 推断
        return _client
    s = get_settings()
    if not s.hybrid_enabled:
        return None
    sig = f"{s.embedding_provider}|{s.embedding_model}|{s.litellm_base_url}"
    if _client is not None and sig == _client_sig:
        return _client
    with _client_lock:
        if _client_sig == "__injected__":
            return _client
        if _client is None or sig != _client_sig:
            _client = _build_client(s)
            _client_sig = sig
    return _client


def _build_client(s) -> EmbeddingClient | None:
    if s.embedding_provider == "proxy":
        key = s.litellm_api_key.get_secret_value()
        if not (s.litellm_base_url and key and s.embedding_model):
            log.warning("embed_client_unavailable", reason="missing base_url/key/model")
            return None
        return ProxyEmbeddingClient(
            s.litellm_base_url, key, s.embedding_model, s.embedding_batch_size
        )
    # provider=local 预留：本地 onnxruntime 中文模型（本期未实现）
    log.warning("embed_provider_unsupported", provider=s.embedding_provider)
    return None


def set_embed_client(client: EmbeddingClient | None) -> None:
    """测试注入：直接设定 client（不走 config 推断）。client=None 模拟"向量不可用"。"""
    global _client, _client_sig
    _client = client
    _client_sig = "__injected__"


def reset_embed_client() -> None:
    """清除注入/缓存，下次 get_embed_client 按 config 重建（测试 teardown 用）。"""
    global _client, _client_sig
    _client = None
    _client_sig = None
