"""md 入库 → PageIndex 建树 → BM25 索引（搬自 pageindex-agent/kb_agent/ingest/run.py）。

产物落文件存储（settings.data_dir 下 workspace/ 树、indexes/ 索引、catalog/ 目录），
不进 DB——树/索引是引擎原生文件产物。建树 LLM 走 LiteLLM Proxy（见 ingest_default）。
"""

import hashlib
import json
import time
from pathlib import Path

from app.core.config import get_settings
from app.core.fsutil import write_json_atomic, write_text_atomic
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


def rebuild_from_workspace() -> dict:
    """轻量重建：用已建好的 workspace 树重建 BM25/向量/目录，**不重跑 VLM、不重建树、不重新摘要**。

    供删除文档后调用——删一个文档不该把其余文档全部重新解析 + 逐节点 LLM 摘要（慢且烧钱，
    之前 delete 走 ingest_default 会卡在 "Generating summaries" 数分钟，表现为"删不掉"）。
    剩余文档的树已落盘在 workspace/*.json，这里直接复用：BM25 本地重建、向量按 hash 复用旧向量、
    目录从树重写。catalog 最后写（mtime 是 TreeStore 重载触发器）。
    """
    settings = get_settings()
    out = settings.data_dir
    with INDEX_LOCK:
        ws = out / "workspace"
        domain_dict = out / "domain_dict_auto.txt"
        # 先清空域词典再由存活文档重建：删掉的文档词条不残留（write_domain_terms 只追加）
        domain_dict.unlink(missing_ok=True)
        cards: list = []
        meta: dict = {}
        all_records: list = []
        for f in sorted(ws.glob("doc_*.json")) if ws.exists() else []:
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("workspace_doc_unreadable_skipped", path=str(f))
                continue
            doc_id = doc["id"]
            kb = doc.get("kb", "default")
            md_path = Path(doc.get("path", ""))
            full_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            search_text = doc.get("doc_name", "") + "\n" + full_text
            cards.append({**build_card(doc_id, doc, search_text, domain_dict), "kb": kb})
            meta[doc_id] = {
                "type": doc.get("type", "md"),
                "kb": kb,
                "doc_name": doc.get("doc_name", ""),
                "doc_description": doc.get("doc_description", ""),
                "path": doc.get("path", ""),
                "line_count": doc.get("line_count", 0),
            }
            all_records.extend(iter_nodes(doc_id, doc))

        idx_dir = out / "indexes"
        if all_records:
            build_index(all_records).save(idx_dir)
            _build_vector_index(all_records, idx_dir)  # 节点未变 → 向量按 hash 全部复用，不调 API
        else:
            # 删到一篇不剩：清掉索引，避免残留指向已删文档
            import shutil
            for name in ("meta.json", "embeddings.npy", "vec_meta.json"):
                (idx_dir / name).unlink(missing_ok=True)
            shutil.rmtree(idx_dir / "bm25", ignore_errors=True)

        write_json_atomic(ws / "_meta.json", meta, indent=2)
        write_json_atomic(out / "catalog" / "document_catalog.json", cards, indent=2)  # commit-last
        log.info("rebuilt_from_workspace", docs=len(cards), nodes=len(all_records))
        return {"docs": len(cards), "nodes": len(all_records)}


def _resolve_doc_id(ws: Path, doc_name: str, md_path: Path, rel: Path) -> str:
    """据 doc_name 算 doc_id；与已存在的【不同文件】同名时加路径派生后缀消歧
    （与 ingest_dir 的冲突处理一致）。同一文件再次入库 → 返回同 id 覆盖（即重跑/重新解析）。"""
    base = make_doc_id(doc_name)
    f = ws / f"{base}.json"
    if f.exists():
        try:
            existing = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        if Path(existing.get("path", "")).resolve() == md_path:
            return base  # 同一文件重新入库 → 覆盖（重跑/重新解析同一篇）
        suffix = hashlib.sha1(
            str(rel).encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:4]
        return f"{base}_{suffix}"
    return base


def build_reparse_candidate(
    md_path: str | Path,
    *,
    doc_id: str,
    kb: str,
    final_md_path: str | Path,
) -> dict:
    """Build a workspace document from a temporary md without touching live data."""
    settings = get_settings()
    settings.apply_litellm_env()
    md_path = Path(md_path).resolve()
    final_md_path = Path(final_md_path).resolve()
    tree = build_tree(str(md_path), model=settings.index_model)
    annotate_pages(md_path, tree["structure"])
    return {
        "id": doc_id,
        "type": "md",
        "kb": kb,
        "path": str(final_md_path),
        "doc_name": tree["doc_name"],
        "doc_description": tree.get("doc_description", ""),
        "line_count": tree.get("line_count", 0),
        "structure": tree["structure"],
    }


def _snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _restore(path: Path, data: bytes | None) -> None:
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _snapshot_dir(path: Path) -> dict | None:
    if not path.exists():
        return None
    files: dict[str, bytes] = {}
    dirs: set[str] = set()
    for p in path.rglob("*"):
        rel = str(p.relative_to(path))
        if p.is_dir():
            dirs.add(rel)
        elif p.is_file():
            files[rel] = p.read_bytes()
    return {"dirs": dirs, "files": files}


def _restore_dir(path: Path, snapshot: dict | None) -> None:
    import shutil

    if snapshot is None:
        shutil.rmtree(path, ignore_errors=True)
        return
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    for rel in snapshot["dirs"]:
        (path / rel).mkdir(parents=True, exist_ok=True)
    for rel, data in snapshot["files"].items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _replace_sidecar(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    else:
        dst.unlink(missing_ok=True)


def commit_reparse_candidate(
    doc: dict,
    md_text: str,
    *,
    tmp_pagemap: str | Path | None = None,
    tmp_ocr: str | Path | None = None,
) -> dict:
    """Publish a validated reparse candidate and rebuild indexes.

    If commit or rebuild fails, restore the previous live md/sidecars/workspace doc.
    """
    with INDEX_LOCK:
        settings = get_settings()
        md_path = Path(doc["path"])
        ws_path = settings.data_dir / "workspace" / f"{doc['id']}.json"
        pagemap_path = Path(str(md_path) + ".pagemap.json")
        ocr_path = Path(str(md_path) + ".ocr.json")

        snapshots = {
            md_path: _snapshot(md_path),
            pagemap_path: _snapshot(pagemap_path),
            ocr_path: _snapshot(ocr_path),
            ws_path: _snapshot(ws_path),
            settings.data_dir / "catalog" / "document_catalog.json": _snapshot(
                settings.data_dir / "catalog" / "document_catalog.json"
            ),
            settings.data_dir / "workspace" / "_meta.json": _snapshot(
                settings.data_dir / "workspace" / "_meta.json"
            ),
            settings.data_dir / "domain_dict_auto.txt": _snapshot(
                settings.data_dir / "domain_dict_auto.txt"
            ),
        }
        indexes_snapshot = _snapshot_dir(settings.data_dir / "indexes")
        try:
            write_text_atomic(md_path, md_text)
            if tmp_pagemap is not None:
                _replace_sidecar(Path(tmp_pagemap), pagemap_path)
            if tmp_ocr is not None and Path(tmp_ocr).exists():
                _replace_sidecar(Path(tmp_ocr), ocr_path)
            else:
                ocr_path.unlink(missing_ok=True)
            write_json_atomic(ws_path, doc, indent=2)
            result = rebuild_from_workspace()
            log.info("reparse_committed", doc_id=doc["id"], doc_name=doc.get("doc_name"))
            return result
        except BaseException:
            for path, data in snapshots.items():
                _restore(path, data)
            _restore_dir(settings.data_dir / "indexes", indexes_snapshot)
            raise


def ingest_one(md_path) -> dict:
    """增量入库单篇 md：只对这一篇 build_tree + 落 workspace，再用 rebuild_from_workspace
    轻量重建索引/目录（其余文档复用已落盘的树，不重跑摘要、向量按 hash 复用）。

    与 delete 的轻量重建对称——上传/审核一篇文档不该把全库重新建树 + 逐节点 LLM 摘要
    （那会随库增大越来越慢/烧钱）。kb 由 md 在 data/md 下的子目录推出，与 ingest_dir 一致。
    """
    settings = get_settings()
    settings.apply_litellm_env()
    md_path = Path(md_path).resolve()
    md_root = (settings.data_dir / "md").resolve()
    with INDEX_LOCK:  # RLock：本函数持锁，内部 rebuild_from_workspace 可重入获取
        ws = settings.data_dir / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        try:
            rel = md_path.relative_to(md_root)
            kb = rel.parts[0] if len(rel.parts) > 1 else "default"
        except ValueError:  # md 不在 data/md 下（理论不会，防御）
            rel = Path(md_path.name)
            kb = "default"
        _t = time.perf_counter()
        tree = build_tree(str(md_path), model=settings.index_model)
        t_build = time.perf_counter() - _t  # 含逐节点摘要(LLM)，通常是建树大头
        log.info("timing_build_tree", secs=round(t_build, 1),
                 md_lines=tree.get("line_count"))
        annotate_pages(md_path, tree["structure"])
        doc_id = _resolve_doc_id(ws, tree["doc_name"], md_path, rel)
        doc = {
            "id": doc_id,
            "type": "md",
            "kb": kb,
            "path": str(md_path),
            "doc_name": tree["doc_name"],
            "doc_description": tree.get("doc_description", ""),
            "line_count": tree.get("line_count", 0),
            "structure": tree["structure"],
        }
        write_json_atomic(ws / f"{doc_id}.json", doc, indent=2)
        log.info("ingested_one", doc_id=doc_id, doc_name=tree["doc_name"], kb=kb)
        # 复用删除路径的轻量重建：BM25 本地重建、向量按 hash 复用、目录从树重写
        _t = time.perf_counter()
        result = rebuild_from_workspace()
        log.info("timing_rebuild_index", secs=round(time.perf_counter() - _t, 1),
                 build_tree_secs=round(t_build, 1))
        return result


def ingest_default() -> dict:
    """便捷入口：扫 settings.data_dir/md 建树，落 settings.data_dir，模型走 settings.index_model。

    建树前把 LiteLLM Proxy 凭证桥接到 OPENAI_* env（pageindex 建树内部纯靠 env）。
    注意：内部 build_tree 用 asyncio.run，必须从同步上下文（如 FastAPI 同步 def 路由的线程池）调用。
    """
    settings = get_settings()
    settings.apply_litellm_env()
    return ingest_dir(settings.data_dir / "md", settings.data_dir, settings.index_model)
