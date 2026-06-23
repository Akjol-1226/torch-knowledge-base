"""md 入库 → PageIndex 建树 → BM25 索引（搬自 pageindex-agent/kb_agent/ingest/run.py）。

产物落文件存储（settings.data_dir 下 workspace/ 树、indexes/ 索引、catalog/ 目录），
不进 DB——树/索引是引擎原生文件产物。建树 LLM 走 LiteLLM Proxy（见 ingest_default）。
"""

import json
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.retrieval import build_index, iter_nodes
from app.modules.ingest.catalog import build_card, make_doc_id
from app.modules.ingest.page_locator import annotate_pages
from app.modules.ingest.tree import build_tree

log = get_logger("ingest.tree")


def ingest_dir(md_dir, out, model: str) -> dict:
    """把 md_dir 下所有 .md 建成树 + 目录 + BM25 索引，落到 out 目录。返回统计。"""
    md_dir = Path(md_dir)
    out = Path(out)
    ws = out / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (out / "catalog").mkdir(parents=True, exist_ok=True)
    domain_dict = out / "domain_dict_auto.txt"

    cards: list = []
    meta: dict = {}
    all_records: list = []

    # 递归扫 data/md/**/*.md：直接子目录名即知识库（kb），根目录下的归 "default"
    for md_path in sorted(md_dir.rglob("*.md")):
        rel = md_path.relative_to(md_dir)
        kb = rel.parts[0] if len(rel.parts) > 1 else "default"
        full_text = md_path.read_text(encoding="utf-8")
        tree = build_tree(str(md_path), model=model)
        # 据 <md>.pagemap.json 给节点标注 PDF 页码（docparse 入库时落盘；裸 md 无侧车则跳过）
        annotate_pages(md_path, tree["structure"])
        doc_id = make_doc_id(tree["doc_name"])

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
        (ws / f"{doc_id}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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

    (ws / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "catalog" / "document_catalog.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if all_records:
        build_index(all_records).save(out / "indexes")

    log.info("ingested", docs=len(cards), nodes=len(all_records), out=str(out))
    return {"docs": len(cards), "nodes": len(all_records)}


def ingest_default() -> dict:
    """便捷入口：扫 settings.data_dir/md 建树，落 settings.data_dir，模型走 settings.index_model。

    建树前把 LiteLLM Proxy 凭证桥接到 OPENAI_* env（pageindex 建树内部纯靠 env）。
    注意：内部 build_tree 用 asyncio.run，必须从同步上下文（如 FastAPI 同步 def 路由的线程池）调用。
    """
    settings = get_settings()
    settings.apply_litellm_env()
    return ingest_dir(settings.data_dir / "md", settings.data_dir, settings.index_model)
