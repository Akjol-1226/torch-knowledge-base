"""md 入库 → PageIndex 建树 → BM25 索引（搬自 pageindex-agent/kb_agent/ingest/run.py）。

产物落文件存储（settings.data_dir 下 workspace/ 树、indexes/ 索引、catalog/ 目录），
不进 DB——树/索引是引擎原生文件产物。建树 LLM 走 LiteLLM Proxy（见 ingest_default）。
"""

import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.core.fsutil import write_json_atomic
from app.core.logging import get_logger
from app.core.retrieval import VectorIndex, build_index, get_embed_client, iter_nodes
from app.modules.ingest.catalog import build_card, make_doc_id
from app.modules.ingest.locks import INDEX_LOCK
from app.modules.ingest.page_locator import annotate_pages
from app.modules.ingest.tree import build_tree

log = get_logger("ingest.tree")


def _build_vector_index(records: list, idx_dir) -> None:
    """据 records 建/更新向量索引，落 idx_dir。

    向量是增强项：无 client 或失败都不阻断入库（BM25 已建好）。
    """
    client = get_embed_client()
    if client is None:
        log.info("vector_index_skipped", reason="no embed client (hybrid off / no model)")
        return
    try:
        old = VectorIndex.load(idx_dir)  # 复用未变节点的旧向量，省 API 调用
        max_chars = get_settings().embedding_max_chars
        VectorIndex.build(records, client, max_chars, old=old).save(idx_dir)
    except Exception:
        log.exception("vector_index_build_failed")  # 降级：本次只有 BM25


def ingest_dir(md_dir, out, model: str) -> dict:
    """把 md_dir 下所有 .md 建成树 + 目录 + BM25 索引，落到 out 目录。返回统计。

    全程持 INDEX_LOCK 串行化（多入口并发会互相覆盖）；catalog/_meta/workspace 用原子写
    （临时文件 + os.replace）发布，避免并发读到截断 JSON。
    """
    md_dir = Path(md_dir)
    out = Path(out)
    with INDEX_LOCK:
        ws = out / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (out / "catalog").mkdir(parents=True, exist_ok=True)
        domain_dict = out / "domain_dict_auto.txt"

        cards: list = []
        meta: dict = {}
        all_records: list = []
        seen_ids: set[str] = set()

        # 递归扫 data/md/**/*.md：直接子目录名即知识库（kb），根目录下的归 "default"
        # 排序遍历 → doc_id 冲突消歧是确定性的（同名不同文件，靠路径派生稳定后缀）
        for md_path in sorted(md_dir.rglob("*.md")):
            rel = md_path.relative_to(md_dir)
            kb = rel.parts[0] if len(rel.parts) > 1 else "default"
            full_text = md_path.read_text(encoding="utf-8")
            tree = build_tree(str(md_path), model=model)
            # 据 <md>.pagemap.json 给节点标注 PDF 页码（docparse 入库时落盘；裸 md 无侧车则跳过）
            annotate_pages(md_path, tree["structure"])
            doc_id = make_doc_id(tree["doc_name"])
            if doc_id in seen_ids:
                # 同名不同文件：用路径派生稳定后缀消歧，避免后者覆盖前者（静默丢文档）
                suffix = hashlib.sha1(
                    str(rel).encode("utf-8"), usedforsecurity=False
                ).hexdigest()[:4]
                doc_id = f"{doc_id}_{suffix}"
                log.warning("doc_id_collision", doc_name=tree["doc_name"], doc_id=doc_id)
            seen_ids.add(doc_id)

            doc = {
                "id": doc_id,
                "type": "md",
                "kb": kb,
                "path": str(md_path.resolve()),
                "doc_name": tree["doc_name"],
                "doc_description": tree.get("doc_description", ""),
                "line_count": tree.get("line_count", 0),
                "structure": tree["structure"],
            }
            write_json_atomic(ws / f"{doc_id}.json", doc, indent=2)
            meta[doc_id] = {
                "type": "md",
                "kb": kb,
                "doc_name": doc["doc_name"],
                "doc_description": doc["doc_description"],
                "path": doc["path"],
                "line_count": doc["line_count"],
            }

            # 把 doc_name 并入搜索文本，确保文件名中的项目号/型号也被抽出写进域词典
            search_text = tree.get("doc_name", "") + "\n" + full_text
            cards.append({**build_card(doc_id, tree, search_text, domain_dict), "kb": kb})
            all_records.extend(iter_nodes(doc_id, tree))

        # 清理本次不再存在的旧 workspace 文件（删文档后残留会被 TreeStore 重新加载）
        for stale in ws.glob("doc_*.json"):
            if stale.stem not in seen_ids:
                stale.unlink(missing_ok=True)

        # 先写索引，最后才写 catalog（catalog mtime 是 TreeStore 重载触发器）——
        # 这样读端一旦检测到 catalog 变化，BM25/向量索引必然已写完，不会读到新目录配旧索引。
        if all_records:
            build_index(all_records).save(out / "indexes")
            _build_vector_index(all_records, out / "indexes")  # 混合检索：同批建向量

        write_json_atomic(ws / "_meta.json", meta, indent=2)
        write_json_atomic(out / "catalog" / "document_catalog.json", cards, indent=2)  # commit-last

        log.info("ingested", docs=len(cards), nodes=len(all_records), out=str(out))
        return {"docs": len(cards), "nodes": len(all_records)}


def build_vectors_default() -> dict:
    """补建向量索引（不重跑 VLM/不重建树）：直接读已有 data/workspace/doc_*.json 的树。

    给"代码升级前已入库、缺 embeddings.npy"的老文档用。新文档入库时已自动建向量，无需调它。
    """
    settings = get_settings()
    settings.apply_litellm_env()
    with INDEX_LOCK:
        ws = settings.data_dir / "workspace"
        records: list = []
        for f in sorted(ws.glob("doc_*.json")) if ws.exists() else []:
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("workspace_doc_unreadable_skipped", path=str(f))
                continue
            records.extend(iter_nodes(doc["id"], doc))
        if not records:
            return {"nodes": 0, "built": False, "reason": "no workspace docs"}
        client = get_embed_client()
        if client is None:
            return {"nodes": len(records), "built": False, "reason": "no embed client"}
        idx_dir = settings.data_dir / "indexes"
        old = VectorIndex.load(idx_dir)
        VectorIndex.build(records, client, settings.embedding_max_chars, old=old).save(idx_dir)
        return {"nodes": len(records), "built": True}


def ingest_default() -> dict:
    """便捷入口：扫 settings.data_dir/md 建树，落 settings.data_dir，模型走 settings.index_model。

    建树前把 LiteLLM Proxy 凭证桥接到 OPENAI_* env（pageindex 建树内部纯靠 env）。
    注意：内部 build_tree 用 asyncio.run，必须从同步上下文（如 FastAPI 同步 def 路由的线程池）调用。
    """
    settings = get_settings()
    settings.apply_litellm_env()
    return ingest_dir(settings.data_dir / "md", settings.data_dir, settings.index_model)
