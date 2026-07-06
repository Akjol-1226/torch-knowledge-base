# B1 Document Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build B1 “基于历史框架生成新文档”: choose a historical knowledge-base document as a blueprint, summarize its framework, accept a natural-language change request, produce a reviewable change plan, and render a copyable draft without modifying or ingesting the original document.

**Architecture:** Add a new `docgen` business module under `backend/app/modules/docgen/`. It reads existing PageIndex/TreeStore artifacts through `app.core.retrieval`, stores only temporary generation sessions under `backend/data/docgen/`, and exposes `/docgen/*` APIs consumed by the existing single-page HTML frontend. Draft generation is conservative: LLM creates structured plan items, while deterministic renderer applies only accepted exact replacements to the blueprint Markdown.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, LangChain `ChatOpenAI` through LiteLLM Proxy, existing `TreeStore` retrieval, file JSON storage with atomic writes, static HTML/Tailwind frontend in `product/design/v0-mockup-v2.html`.

---

## 0. Scope, Defaults, And Non-Negotiables

### Source References

- PRD: `product/prd/b1-b2-doc-authoring.md`
- B1 design mockup: `product/design/b1-doc-generation-mockup.html`
- Current served frontend: `product/design/v0-mockup-v2.html`
- Current root route: `backend/app/main.py`
- Current retrieval tools: `backend/app/modules/chat/tools.py`
- Current retrieval store: `backend/app/core/retrieval/treestore.py`

### B1 Requirements Covered

- Select one historical document as blueprint.
- Record blueprint by `doc_id`.
- Agent reads PageIndex outline and summaries first, not full text.
- Agent returns blueprint summary and asks what new document the user wants.
- User gives natural-language generation request.
- Agent locates impacted nodes and reads only needed node text.
- Agent produces structured change plan.
- Plan item shows location, old value, new value, source label, reason, and citation handles.
- User can deselect plan items.
- Confirmed items render a new draft.
- Draft shows changed content, AI-inferred content, unchanged chapters, “初稿·待审核”, and copy button.

### Explicit Non-Goals For First Implementation

- Do not implement B2 original-document editing.
- Do not modify the original document.
- Do not write generated drafts into `data/md`, `data/workspace`, `data/catalog`, or `data/indexes`.
- Do not create `.docx`.
- Do not implement Markdown/JSON export. The standalone B1 mockup text saying “可导出 Markdown / 结构化 JSON” conflicts with the PRD and must not be carried into the integrated page.
- Do not implement external reference upload in B1. Hide the mockup button for now.
- Do not implement workflow approval, version rollback, or draft ingestion.
- Do not hardcode Torch-specific calculation formulas in backend business code.

### Default Decisions To Avoid Blocking Implementation

- **Copy format:** copy plain readable text only. The right-side preview may render Markdown and UI highlights, but the clipboard action copies plain text without highlight marks or UI labels.
- **Session persistence:** persist sessions as JSON files under `settings.data_dir / "docgen" / "sessions"`. No automatic cleanup in the first implementation.
- **Model:** reuse `settings.chat_model` and the existing LiteLLM Proxy settings. Do not add a new `DOCGEN_MODEL` setting in the first pass.
- **Unchanged-section labels:** “原样沿用” is a UI-only preview label. It must not be inserted into copied draft text.
- **Highlight navigation:** the draft preview must include a “下一个高亮” button that scrolls to the next changed/AI-inferred highlight.
- **Calculation tool:** implement a safe arithmetic calculator. The AI decides the formula/expression from context; backend only evaluates arithmetic to reduce numeric mistakes, records the expression/result, and marks inferred values as review-required. Do not hardcode Torch business formulas. If an AI-proposed `new_text` contains a numeric value that conflicts with `calculation_result`, the item must be marked unresolved and must not be auto-applied to the draft.
- **Blueprint metadata:** first version shows only `doc_name`, `doc_description`, `kb`, `chapter_count`, and `node_count`. The card should visibly include which knowledge base the blueprint belongs to.
- **Blueprint search:** do not implement a blueprint text search box in the first version. Future filtering can be added after B1 is working.
- **Duplicate old text:** do not build complex disambiguation in the first version. Keep the conservative renderer behavior: if exact replacement is ambiguous, do not auto-apply that item and show a warning.
- **Permissions/audit:** do not add new permission or audit behavior in B1 first version. Keep the route available like existing local features.
- **Blueprint-only plan scope:** B1 plan generation must only read nodes from the selected blueprint document. Retrieval may use the blueprint knowledge base as a first filter, but every returned node and every LLM `source_handle` must be validated against `record.doc_id`.
- **Source handle visibility:** keep `source_handles` for backend validation and future traceability. Do not show raw internal handles like `doc_id:node_id` in the first-version UI.
- **Stage 2 UI:** after selecting a blueprint, do not show “正在读取/正在概括/已通读” process text. A neutral loading skeleton is acceptable; once the API returns, show only the blueprint summary plus the one follow-up question.
- **Production code style:** all production Python snippets in this plan must be wrapped to the repo's Ruff line-length limit. If a copied snippet exceeds 100 columns, wrap it during implementation rather than adding ignores.
- **Frontend integration:** integrate into `product/design/v0-mockup-v2.html`; keep `product/design/b1-doc-generation-mockup.html` as reference only.

---

## 1. File Map

### Create

- `backend/app/core/retrieval/store_provider.py`
  - Shared lazy `TreeStore` provider previously embedded in `chat.tools`.
- `backend/app/core/retrieval/document_reader.py`
  - Reads workspace metadata and parsed Markdown by `doc_id` without importing `ingest`.
- `backend/app/modules/docgen/__init__.py`
  - Module marker.
- `backend/app/modules/docgen/schemas.py`
  - Pydantic request/response/session schemas.
- `backend/app/modules/docgen/storage.py`
  - JSON session persistence under `data/docgen/sessions`.
- `backend/app/modules/docgen/agent.py`
  - LiteLLM-backed B1 LLM calls plus JSON parsing/validation.
- `backend/app/modules/docgen/calculator.py`
  - Thin calculation tool result structure.
- `backend/app/modules/docgen/renderer.py`
  - Deterministic draft renderer and exact-replacement logic.
- `backend/app/modules/docgen/service.py`
  - B1 orchestration.
- `backend/app/modules/docgen/router.py`
  - `/docgen/*` routes.
- `backend/app/modules/docgen/prompts/blueprint_summary.md`
  - Prompt for blueprint summary.
- `backend/app/modules/docgen/prompts/change_plan.md`
  - Prompt for change plan JSON.
- `backend/tests/unit/retrieval/test_store_provider.py`
  - Shared store provider tests.
- `backend/tests/unit/retrieval/test_document_reader.py`
  - Document reader tests.
- `backend/tests/unit/docgen/test_storage.py`
  - Session storage tests.
- `backend/tests/unit/docgen/test_renderer.py`
  - Draft replacement/highlight tests.
- `backend/tests/unit/docgen/test_service.py`
  - B1 service orchestration tests with fake store/agent/storage.
- `backend/tests/integration/test_docgen_router.py`
  - API happy path and validation tests.

### Modify

- `backend/app/modules/chat/tools.py`
  - Replace local `_store`/`get_store` implementation with shared `store_provider`; keep public `get_store()` and `set_store()` wrappers so existing tests keep working.
- `backend/app/main.py`
  - Import and register `docgen_router`.
- `product/design/v0-mockup-v2.html`
  - Add “生成文档” nav item and B1 page.
  - Add B1 CSS classes.
  - Add B1 JavaScript state and API calls.
  - Add “用作蓝本生成” button in knowledge-base document list.

### Do Not Touch

- `backend/data_eval/`
- `backend/data/md/`
- `backend/data/workspace/`
- `backend/data/catalog/`
- `backend/data/indexes/`
- `product/design/b1-doc-generation-mockup.html` unless the user explicitly asks to update the standalone prototype.

---

## 2. API Contract

### `GET /docgen/blueprints`

Query params:

- `kbs`: optional comma-separated knowledge-base names.

Response:

```json
{
  "items": [
    {
      "doc_id": "doc_x",
      "doc_name": "G03 涂布工序工艺规程",
      "doc_description": "工艺规程",
      "kb": "default",
      "chapter_count": 7,
      "node_count": 22
    }
  ]
}
```

Error behavior:

- If catalog is empty, return `{"items":[]}` with HTTP 200.
- Do not call LLM.
- Do not read Markdown full text.

### `POST /docgen/sessions`

Request:

```json
{
  "doc_id": "doc_x"
}
```

Response:

```json
{
  "session_id": "b1_20260702_abcdef123456",
  "doc_id": "doc_x",
  "status": "waiting_for_requirement",
  "blueprint": {
    "doc_id": "doc_x",
    "doc_name": "G03 涂布工序工艺规程",
    "doc_description": "工艺规程",
    "kb": "default",
    "chapter_count": 7,
    "node_count": 22
  },
  "summary": "这份蓝本主要用于……",
  "outline": [
    {
      "node_id": "doc_x:0001",
      "title": "1 适用范围",
      "summary": "……",
      "depth": 1
    }
  ],
  "question": "你想基于它做份什么新文档？"
}
```

Error behavior:

- Unknown `doc_id` returns HTTP 404.
- LLM failure returns HTTP 502 with `detail`.
- Session is saved only after summary succeeds.

### `GET /docgen/sessions/{session_id}`

Response is the current session record, including `plan` and `draft` if available.

Error behavior:

- Unknown or unsafe `session_id` returns HTTP 404.

### `POST /docgen/sessions/{session_id}/plan`

Request:

```json
{
  "message": "做 1206 规格的新产品，涂布厚度改成 15±2μm，型号跟着改，其它沿用。相关的尺寸、电容值你按规格重算一下。"
}
```

Response:

```json
{
  "session_id": "b1_20260702_abcdef123456",
  "status": "plan_ready",
  "plan": [
    {
      "id": "chg_001",
      "node_id": "doc_x:0002",
      "location": "第 2 章 · 适用范围",
      "old_text": "0805",
      "new_text": "1206",
      "source_type": "user",
      "source_label": "你指定",
      "reason": "用户明确要求改为 1206 规格。",
      "source_handles": ["doc_x:0002"],
      "selected": true,
      "confidence": 0.95,
      "warning": ""
    },
    {
      "id": "chg_002",
      "node_id": "doc_x:0005",
      "location": "附表 1 · 产品规格",
      "old_text": "额定电容 220 pF",
      "new_text": "额定电容 450.56 pF",
      "source_type": "ai",
      "source_label": "AI推理",
      "reason": "根据用户要求重算电容值，需研发人员核对。",
      "source_handles": ["doc_x:0005"],
      "calculation_expression": "220 * (3.2 * 1.6) / (2.0 * 1.25)",
      "calculation_result": "450.56",
      "calculation_note": "计算器仅计算 AI 给出的算式，不代表业务公式已审核。",
      "selected": true,
      "confidence": 0.55,
      "warning": "AI 推理值，必须人工核对。"
    }
  ],
  "warnings": []
}
```

Error behavior:

- Empty `message` returns HTTP 422.
- Unknown session returns HTTP 404.
- If retrieval index is unavailable, return HTTP 503 with detail.
- LLM JSON validation failure returns HTTP 502 and stores no new plan.

### `PATCH /docgen/sessions/{session_id}/plan`

Request:

```json
{
  "items": [
    {"id": "chg_001", "selected": true},
    {"id": "chg_002", "selected": false}
  ]
}
```

Response:

```json
{
  "session_id": "b1_20260702_abcdef123456",
  "status": "plan_ready",
  "plan": []
}
```

Behavior:

- Preserve existing fields.
- Only update known item ids.
- Unknown item ids return HTTP 400 with a list of invalid ids.

### `POST /docgen/sessions/{session_id}/draft`

Request:

```json
{
  "accepted_item_ids": ["chg_001"]
}
```

Response:

```json
{
  "session_id": "b1_20260702_abcdef123456",
  "status": "draft_ready",
  "title": "G03 涂布工序工艺规程（初稿）",
  "markdown": "# ...",
  "copy_text": "可直接复制粘贴到 Word 的纯文本正文",
  "highlighted_markdown": "# ... <mark class=\"docgen-change-user\">1206</mark> ...",
  "highlight_count": 1,
  "applied_item_ids": ["chg_001"],
  "skipped_item_ids": ["chg_002"],
  "unresolved_item_ids": [],
  "warnings": [
    "初稿仅供参考，必须经研发人员审核。"
  ]
}
```

Behavior:

- Apply only `accepted_item_ids`.
- If request omits `accepted_item_ids`, apply all currently selected plan items.
- If an item has `selected=false`, do not apply it even if the request includes its id.
- If `old_text` is absent or appears multiple times, do not replace it; return the item id in `unresolved_item_ids`.
- Never write draft into ingest/index directories.

---

## 3. Data Model Details

Use these exact enum values so frontend and backend stay aligned:

```python
class DocgenStatus(str, Enum):
    waiting_for_requirement = "waiting_for_requirement"
    plan_ready = "plan_ready"
    draft_ready = "draft_ready"

class ChangeSourceType(str, Enum):
    user = "user"
    ai = "ai"
```

Use `source_label` for display text:

- `user` -> `你指定`
- `ai` -> `AI推理`

Plan item ids are generated by backend as `chg_001`, `chg_002`, etc. Do not trust ids produced by the LLM.

Session ids are generated by backend as:

```python
f"b1_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(6)}"
```

Session file path:

```python
get_settings().data_dir / "docgen" / "sessions" / f"{safe_name(session_id)}.json"
```

---

## 4. Implementation Tasks

### Task 1: Extract Shared TreeStore Provider

**Files:**

- Create: `backend/app/core/retrieval/store_provider.py`
- Modify: `backend/app/modules/chat/tools.py`
- Test: `backend/tests/unit/retrieval/test_store_provider.py`
- Existing tests to keep passing: `backend/tests/unit/test_tools.py`

- [ ] **Step 1: Write failing provider tests**

Create `backend/tests/unit/retrieval/test_store_provider.py`:

```python
import json

from app.core.retrieval import store_provider
from app.core.retrieval.treestore import TreeStore


def _make_data_dir(tmp_path):
    ws = tmp_path / "workspace"
    cat = tmp_path / "catalog"
    idx = tmp_path / "indexes"
    ws.mkdir(parents=True)
    cat.mkdir(parents=True)
    idx.mkdir(parents=True)
    doc = {
        "id": "doc_a",
        "doc_name": "工艺文件",
        "doc_description": "工艺",
        "kb": "default",
        "line_count": 3,
        "structure": [
            {
                "title": "1 范围",
                "node_id": "0001",
                "line_num": 1,
                "summary": "范围摘要",
                "text": "0805 产品",
                "nodes": [],
            }
        ],
    }
    (ws / "doc_a.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (cat / "document_catalog.json").write_text(
        json.dumps(
            [
                {
                    "doc_id": "doc_a",
                    "doc_name": "工艺文件",
                    "doc_description": "工艺",
                    "kb": "default",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_get_tree_store_uses_settings_data_dir(tmp_path, monkeypatch):
    data_dir = _make_data_dir(tmp_path)
    monkeypatch.setattr(
        store_provider,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": data_dir})(),
    )
    store_provider.clear_tree_store_for_tests()

    store = store_provider.get_tree_store()

    assert isinstance(store, TreeStore)
    assert store.list_catalog()[0]["doc_id"] == "doc_a"


def test_set_tree_store_for_tests_injects_store(tmp_path, monkeypatch):
    data_dir = _make_data_dir(tmp_path)
    injected = TreeStore(data_dir)
    monkeypatch.setattr(
        store_provider,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": data_dir})(),
    )

    store_provider.set_tree_store_for_tests(injected)

    assert store_provider.get_tree_store() is injected
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run from `backend/`:

```powershell
uv run pytest tests/unit/retrieval/test_store_provider.py -v
```

Expected result:

```text
ModuleNotFoundError: No module named 'app.core.retrieval.store_provider'
```

- [ ] **Step 3: Implement `store_provider.py`**

Create `backend/app/core/retrieval/store_provider.py`:

```python
from __future__ import annotations

import threading
from pathlib import Path

from app.core.config import get_settings
from app.core.retrieval import TreeStore

_store: TreeStore | None = None
_store_sig: tuple[float | None, float | None] | None = None
_store_lock = threading.Lock()


def _catalog_sig() -> tuple[float | None, float | None]:
    data_dir = Path(get_settings().data_dir)
    catalog = data_dir / "catalog" / "document_catalog.json"
    vec_meta = data_dir / "indexes" / "vec_meta.json"
    return (
        catalog.stat().st_mtime if catalog.exists() else None,
        vec_meta.stat().st_mtime if vec_meta.exists() else None,
    )


def get_tree_store() -> TreeStore:
    global _store, _store_sig
    sig = _catalog_sig()
    if _store is not None and sig == _store_sig:
        return _store
    with _store_lock:
        if _store is None or sig != _store_sig:
            if _store is None:
                _store = TreeStore(get_settings().data_dir)
            else:
                _store = _store.incremental_clone()
            _store_sig = sig
    return _store


def set_tree_store_for_tests(store: TreeStore) -> None:
    global _store, _store_sig
    with _store_lock:
        _store = store
        _store_sig = _catalog_sig()


def clear_tree_store_for_tests() -> None:
    global _store, _store_sig
    with _store_lock:
        _store = None
        _store_sig = None
```

- [ ] **Step 4: Update `chat.tools` to use the shared provider**

Modify `backend/app/modules/chat/tools.py`:

```python
from contextvars import ContextVar

from langchain_core.tools import tool

from app.core.retrieval import TreeStore
from app.core.retrieval.store_provider import (
    get_tree_store,
    set_tree_store_for_tests,
)
```

Remove the local `_store`, `_store_sig`, `_store_lock`, `_catalog_sig()`, and local store construction logic.

Keep these wrappers for existing tests:

```python
def get_store() -> TreeStore:
    return get_tree_store()


def set_store(ts: TreeStore) -> None:
    set_tree_store_for_tests(ts)
```

Do not change `current_kbs`, `_hide_members`, tool definitions, or `KB_TOOLS`.

- [ ] **Step 5: Run provider and existing tool tests**

Run:

```powershell
uv run pytest tests/unit/retrieval/test_store_provider.py tests/unit/test_tools.py -v
```

Expected result:

```text
passed
```

- [ ] **Step 6: Commit checkpoint**

Only if the user asked to commit during implementation:

```powershell
git add backend/app/core/retrieval/store_provider.py backend/app/modules/chat/tools.py backend/tests/unit/retrieval/test_store_provider.py
git commit -m "refactor: 抽取共享TreeStore提供器"
```

### Task 2: Add Core Document Reader

**Files:**

- Create: `backend/app/core/retrieval/document_reader.py`
- Test: `backend/tests/unit/retrieval/test_document_reader.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/retrieval/test_document_reader.py`:

```python
import json

from app.core.retrieval import document_reader


def test_read_document_markdown_by_doc_id(tmp_path, monkeypatch):
    md = tmp_path / "md" / "default" / "工艺.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 工艺\n\n0805 产品", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        json.dumps(
            {
                "id": "doc_a",
                "doc_name": "工艺文件",
                "kb": "default",
                "path": str(md),
                "line_count": 3,
                "structure": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        document_reader,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    doc = document_reader.read_parsed_document("doc_a")

    assert doc is not None
    assert doc.doc_id == "doc_a"
    assert doc.doc_name == "工艺文件"
    assert doc.markdown == "# 工艺\n\n0805 产品"


def test_read_document_returns_none_for_unknown_doc(tmp_path, monkeypatch):
    (tmp_path / "workspace").mkdir()
    monkeypatch.setattr(
        document_reader,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    assert document_reader.read_parsed_document("missing") is None


def test_read_document_rejects_markdown_path_outside_data_md(tmp_path, monkeypatch):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        json.dumps(
            {
                "id": "doc_a",
                "doc_name": "工艺文件",
                "kb": "default",
                "path": str(outside),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        document_reader,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    assert document_reader.read_parsed_document("doc_a") is None
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/retrieval/test_document_reader.py -v
```

Expected result:

```text
ImportError
```

- [ ] **Step 3: Implement document reader**

Create `backend/app/core/retrieval/document_reader.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.fsutil import safe_name


@dataclass(frozen=True)
class ParsedDocument:
    doc_id: str
    doc_name: str
    kb: str
    markdown: str
    workspace: dict


def _workspace_path(doc_id: str) -> Path:
    return get_settings().data_dir / "workspace" / f"{safe_name(doc_id)}.json"


def read_workspace_doc(doc_id: str) -> dict | None:
    path = _workspace_path(doc_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_parsed_document(doc_id: str) -> ParsedDocument | None:
    workspace = read_workspace_doc(doc_id)
    if workspace is None:
        return None
    md_root = (get_settings().data_dir / "md").resolve()
    md_path = Path(workspace.get("path") or "").resolve()
    if not md_path.exists() or md_root not in md_path.parents:
        return None
    markdown = md_path.read_text(encoding="utf-8")
    return ParsedDocument(
        doc_id=doc_id,
        doc_name=workspace.get("doc_name", ""),
        kb=workspace.get("kb", "default"),
        markdown=markdown,
        workspace=workspace,
    )
```

- [ ] **Step 4: Run tests**

Run:

```powershell
uv run pytest tests/unit/retrieval/test_document_reader.py -v
```

Expected result:

```text
passed
```

### Task 3: Define Docgen Schemas

**Files:**

- Create: `backend/app/modules/docgen/__init__.py`
- Create: `backend/app/modules/docgen/schemas.py`
- Test: `backend/tests/unit/docgen/test_schemas.py`

- [ ] **Step 1: Write schema tests**

Create `backend/tests/unit/docgen/test_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from app.modules.docgen.schemas import ChangePlanItem, ChangeSourceType


def test_change_plan_item_sets_display_label_for_user_source():
    item = ChangePlanItem(
        id="chg_001",
        node_id="doc_a:0001",
        location="第 1 章",
        old_text="0805",
        new_text="1206",
        source_type=ChangeSourceType.user,
        reason="用户明确指定",
        source_handles=["doc_a:0001"],
    )

    assert item.source_label == "你指定"
    assert item.selected is True


def test_change_plan_item_sets_ai_warning_defaults():
    item = ChangePlanItem(
        id="chg_002",
        node_id="doc_a:0002",
        location="附表 1",
        old_text="220 pF",
        new_text="470 pF",
        source_type=ChangeSourceType.ai,
        reason="按用户要求推算",
        source_handles=["doc_a:0002"],
    )

    assert item.source_label == "AI推理"
    assert item.warning == "AI 推理值，必须人工核对。"


def test_change_plan_item_requires_source_handles():
    with pytest.raises(ValidationError):
        ChangePlanItem(
            id="chg_003",
            node_id="doc_a:0003",
            location="第 2 章",
            old_text="x",
            new_text="y",
            source_type=ChangeSourceType.user,
            reason="用户指定",
            source_handles=[],
        )
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/docgen/test_schemas.py -v
```

Expected result:

```text
ModuleNotFoundError
```

- [ ] **Step 3: Implement schemas**

Create `backend/app/modules/docgen/__init__.py` as an empty file.

Create `backend/app/modules/docgen/schemas.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


class DocgenStatus(str, Enum):
    waiting_for_requirement = "waiting_for_requirement"
    plan_ready = "plan_ready"
    draft_ready = "draft_ready"


class ChangeSourceType(str, Enum):
    user = "user"
    ai = "ai"


class BlueprintItem(BaseModel):
    doc_id: str
    doc_name: str
    doc_description: str = ""
    kb: str = "default"
    chapter_count: int = 0
    node_count: int = 0


class BlueprintListResponse(BaseModel):
    items: list[BlueprintItem]


class OutlineSummaryItem(BaseModel):
    node_id: str
    title: str
    summary: str = ""
    depth: int = 1


class CreateSessionRequest(BaseModel):
    doc_id: str = Field(min_length=1)


class CreateSessionResponse(BaseModel):
    session_id: str
    doc_id: str
    status: DocgenStatus
    blueprint: BlueprintItem
    summary: str
    outline: list[OutlineSummaryItem]
    question: str


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PlanRequest(BaseModel):
    message: NonEmptyStr


class BlueprintSummaryLLMResponse(BaseModel):
    summary: NonEmptyStr
    question: NonEmptyStr


class ChangePlanLLMItem(BaseModel):
    location: NonEmptyStr
    old_text: NonEmptyStr
    new_text: NonEmptyStr
    source_type: ChangeSourceType
    reason: NonEmptyStr
    source_handles: list[str]
    calculation_expression: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("source_handles")
    @classmethod
    def source_handles_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_handles must not be empty")
        return value


class ChangePlanLLMResponse(BaseModel):
    items: list[ChangePlanLLMItem]
    warnings: list[str] = Field(default_factory=list)


class ChangePlanItem(BaseModel):
    id: str
    node_id: str
    location: str
    old_text: str
    new_text: str
    source_type: ChangeSourceType
    source_label: str = ""
    reason: str
    source_handles: list[str]
    calculation_expression: str = ""
    calculation_result: str = ""
    calculation_note: str = ""
    selected: bool = True
    confidence: float = Field(default=0.5, ge=0, le=1)
    warning: str = ""

    @field_validator("source_handles")
    @classmethod
    def source_handles_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_handles must not be empty")
        return value

    @model_validator(mode="after")
    def fill_display_fields(self) -> "ChangePlanItem":
        if not self.source_label:
            self.source_label = "你指定" if self.source_type == ChangeSourceType.user else "AI推理"
        if self.source_type == ChangeSourceType.ai and not self.warning:
            self.warning = "AI 推理值，必须人工核对。"
        return self


class PlanResponse(BaseModel):
    session_id: str
    status: DocgenStatus
    plan: list[ChangePlanItem]
    warnings: list[str] = Field(default_factory=list)


class PatchPlanSelection(BaseModel):
    id: str
    selected: bool


class PatchPlanRequest(BaseModel):
    items: list[PatchPlanSelection]


class DraftRequest(BaseModel):
    accepted_item_ids: list[str] | None = None


class DraftResponse(BaseModel):
    session_id: str
    status: DocgenStatus
    title: str
    markdown: str
    copy_text: str
    highlighted_markdown: str
    highlight_count: int = 0
    applied_item_ids: list[str]
    skipped_item_ids: list[str]
    unresolved_item_ids: list[str]
    warnings: list[str] = Field(default_factory=list)


class DocgenSessionRecord(BaseModel):
    session_id: str
    doc_id: str
    status: DocgenStatus
    blueprint: BlueprintItem
    summary: str
    outline: list[OutlineSummaryItem]
    question: str
    messages: list[dict] = Field(default_factory=list)
    plan: list[ChangePlanItem] = Field(default_factory=list)
    draft: DraftResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Run schema tests**

Run:

```powershell
uv run pytest tests/unit/docgen/test_schemas.py -v
```

Expected result:

```text
passed
```

### Task 4: Implement Session Storage

**Files:**

- Create: `backend/app/modules/docgen/storage.py`
- Test: `backend/tests/unit/docgen/test_storage.py`

- [ ] **Step 1: Write storage tests**

Create `backend/tests/unit/docgen/test_storage.py`:

```python
from datetime import UTC, datetime

import pytest

from app.modules.docgen import storage
from app.modules.docgen.schemas import (
    BlueprintItem,
    DocgenSessionRecord,
    DocgenStatus,
)


def _record(session_id="b1_20260702_abcdef"):
    now = datetime(2026, 7, 2, tzinfo=UTC)
    return DocgenSessionRecord(
        session_id=session_id,
        doc_id="doc_a",
        status=DocgenStatus.waiting_for_requirement,
        blueprint=BlueprintItem(doc_id="doc_a", doc_name="工艺文件"),
        summary="蓝本摘要",
        outline=[],
        question="你想基于它做份什么新文档？",
        created_at=now,
        updated_at=now,
    )


def test_save_and_load_session_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )
    rec = _record()

    storage.save_session(rec)
    loaded = storage.load_session(rec.session_id)

    assert loaded is not None
    assert loaded.session_id == rec.session_id
    assert loaded.summary == "蓝本摘要"


def test_update_session_locks_read_modify_write(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )
    rec = _record()
    storage.save_session(rec)

    def mutate(item):
        item.summary = "更新后的摘要"
        return item

    updated = storage.update_session(rec.session_id, mutate)
    loaded = storage.load_session(rec.session_id)

    assert updated is not None
    assert loaded is not None
    assert loaded.summary == "更新后的摘要"


def test_load_unknown_session_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    assert storage.load_session("missing") is None


def test_rejects_unsafe_session_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )

    with pytest.raises(ValueError):
        storage.load_session("../bad")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/docgen/test_storage.py -v
```

Expected result:

```text
ImportError
```

- [ ] **Step 3: Implement storage**

Create `backend/app/modules/docgen/storage.py`:

```python
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from app.core.config import get_settings
from app.core.fsutil import safe_name, write_json_atomic
from app.modules.docgen.schemas import DocgenSessionRecord

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _session_dir() -> Path:
    return get_settings().data_dir / "docgen" / "sessions"


def _safe_session_id(session_id: str) -> str:
    safe = safe_name(session_id, maxlen=96)
    if safe != session_id:
        raise ValueError("invalid session_id")
    return safe


def session_path(session_id: str) -> Path:
    return _session_dir() / f"{_safe_session_id(session_id)}.json"


def _lock_for(session_id: str) -> threading.Lock:
    safe = _safe_session_id(session_id)
    with _locks_guard:
        if safe not in _locks:
            _locks[safe] = threading.Lock()
        return _locks[safe]


def _load_session_unlocked(session_id: str) -> DocgenSessionRecord | None:
    path = session_path(session_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return DocgenSessionRecord.model_validate(data)


def _save_session_unlocked(record: DocgenSessionRecord) -> None:
    path = session_path(record.session_id)
    write_json_atomic(
        path,
        record.model_dump(mode="json"),
        indent=2,
    )


def load_session(session_id: str) -> DocgenSessionRecord | None:
    with _lock_for(session_id):
        return _load_session_unlocked(session_id)


def save_session(record: DocgenSessionRecord) -> None:
    with _lock_for(record.session_id):
        _save_session_unlocked(record)


def update_session(
    session_id: str,
    mutator: Callable[[DocgenSessionRecord], DocgenSessionRecord],
) -> DocgenSessionRecord | None:
    with _lock_for(session_id):
        record = _load_session_unlocked(session_id)
        if record is None:
            return None
        updated = mutator(record)
        _save_session_unlocked(updated)
        return updated
```

- [ ] **Step 4: Run storage tests**

Run:

```powershell
uv run pytest tests/unit/docgen/test_storage.py -v
```

Expected result:

```text
passed
```

### Task 5: Implement Renderer

**Files:**

- Create: `backend/app/modules/docgen/renderer.py`
- Test: `backend/tests/unit/docgen/test_renderer.py`

- [ ] **Step 1: Write renderer tests**

Create `backend/tests/unit/docgen/test_renderer.py`:

```python
from app.modules.docgen.renderer import render_draft
from app.modules.docgen.schemas import ChangePlanItem, ChangeSourceType


def _item(
    item_id="chg_001",
    old="0805",
    new="1206",
    selected=True,
    source=ChangeSourceType.user,
):
    return ChangePlanItem(
        id=item_id,
        node_id="doc_a:0001",
        location="第 1 章",
        old_text=old,
        new_text=new,
        source_type=source,
        reason="测试",
        source_handles=["doc_a:0001"],
        selected=selected,
    )


def test_render_draft_applies_selected_exact_replacement():
    result = render_draft(
        title="工艺文件",
        markdown="# 工艺\n\n0805 产品",
        plan=[_item()],
        accepted_item_ids=None,
    )

    assert result.markdown == "# 工艺\n\n1206 产品"
    assert result.applied_item_ids == ["chg_001"]
    assert result.unresolved_item_ids == []
    assert result.highlight_count == 1
    assert '<mark class="docgen-change-user">1206</mark>' in result.highlighted_markdown


def test_render_draft_skips_unselected_item():
    result = render_draft(
        title="工艺文件",
        markdown="# 工艺\n\n0805 产品",
        plan=[_item(selected=False)],
        accepted_item_ids=None,
    )

    assert "0805 产品" in result.markdown
    assert result.applied_item_ids == []
    assert result.skipped_item_ids == ["chg_001"]


def test_render_draft_marks_ambiguous_replacement_unresolved():
    result = render_draft(
        title="工艺文件",
        markdown="0805 产品\n0805 型号",
        plan=[_item()],
        accepted_item_ids=None,
    )

    assert result.markdown == "0805 产品\n0805 型号"
    assert result.unresolved_item_ids == ["chg_001"]
    assert result.applied_item_ids == []


def test_render_draft_marks_ai_change_with_ai_class():
    result = render_draft(
        title="工艺文件",
        markdown="额定电容 220 pF",
        plan=[_item(old="额定电容 220 pF", new="额定电容 470 pF", source=ChangeSourceType.ai)],
        accepted_item_ids=None,
    )

    assert '<mark class="docgen-change-ai">额定电容 470 pF</mark>' in result.highlighted_markdown
    assert any("初稿仅供参考" in w for w in result.warnings)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/docgen/test_renderer.py -v
```

Expected result:

```text
ImportError
```

- [ ] **Step 3: Implement renderer**

Create `backend/app/modules/docgen/renderer.py`:

```python
from __future__ import annotations

import html
import re

from app.modules.docgen.schemas import (
    ChangePlanItem,
    ChangeSourceType,
    DocgenStatus,
    DraftResponse,
)


def _mark_class(item: ChangePlanItem) -> str:
    return "docgen-change-ai" if item.source_type == ChangeSourceType.ai else "docgen-change-user"


def _highlight_text(item: ChangePlanItem) -> str:
    escaped = html.escape(item.new_text)
    return f'<mark class="{_mark_class(item)}">{escaped}</mark>'


def _copy_text(markdown: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", markdown, flags=re.M)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


def render_draft(
    *,
    title: str,
    markdown: str,
    plan: list[ChangePlanItem],
    accepted_item_ids: list[str] | None,
) -> DraftResponse:
    accepted = set(accepted_item_ids) if accepted_item_ids is not None else None
    draft = markdown
    highlighted = markdown
    applied: list[str] = []
    skipped: list[str] = []
    unresolved: list[str] = []
    warnings = ["初稿仅供参考，必须经研发人员审核。"]

    for item in plan:
        if not item.selected:
            skipped.append(item.id)
            continue
        if accepted is not None and item.id not in accepted:
            skipped.append(item.id)
            continue
        count = draft.count(item.old_text)
        if count != 1:
            unresolved.append(item.id)
            warnings.append(
                f"{item.id} 未自动应用：旧文本出现 {count} 次，需人工核对后修改。"
            )
            continue

        draft = draft.replace(item.old_text, item.new_text, 1)
        highlighted = highlighted.replace(item.old_text, _highlight_text(item), 1)
        applied.append(item.id)

    return DraftResponse(
        session_id="",
        status=DocgenStatus.draft_ready,
        title=f"{title}（初稿）",
        markdown=draft,
        copy_text=_copy_text(draft),
        highlighted_markdown=highlighted,
        highlight_count=len(applied),
        applied_item_ids=applied,
        skipped_item_ids=skipped,
        unresolved_item_ids=unresolved,
        warnings=warnings,
    )
```

- [ ] **Step 4: Run renderer tests**

Run:

```powershell
uv run pytest tests/unit/docgen/test_renderer.py -v
```

Expected result:

```text
passed
```

### Task 6: Implement Docgen Agent Shell And Prompts

**Files:**

- Create: `backend/app/modules/docgen/agent.py`
- Create: `backend/app/modules/docgen/prompts/blueprint_summary.md`
- Create: `backend/app/modules/docgen/prompts/change_plan.md`
- Test: `backend/tests/unit/docgen/test_agent.py`

- [ ] **Step 1: Write JSON parser tests**

Create `backend/tests/unit/docgen/test_agent.py`:

```python
import pytest

from app.modules.docgen.agent import extract_json_object


def test_extract_json_object_from_plain_json():
    assert extract_json_object('{"summary":"x"}') == {"summary": "x"}


def test_extract_json_object_from_fenced_json():
    text = '```json\n{"summary":"x","question":"q"}\n```'
    assert extract_json_object(text)["question"] == "q"


def test_extract_json_object_rejects_invalid_text():
    with pytest.raises(ValueError):
        extract_json_object("not json")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/docgen/test_agent.py -v
```

Expected result:

```text
ImportError
```

- [ ] **Step 3: Add prompts**

Create `backend/app/modules/docgen/prompts/blueprint_summary.md`:

```markdown
你是火炬电子研发知识库里的“新文档生成”助手。

任务：用户已经选择一份历史文档作为蓝本。你只能根据给定的目录和节点摘要，概括这份蓝本的用途、主要章节骨架、适用对象，并提出一句反问。

严格规则：
- 不要列出完整参数清单。
- 不要声称已经阅读全文。
- 不要输出 Markdown 表格。
- 只输出 JSON 对象。

JSON schema:
{
  "summary": "2 到 4 句话，说明蓝本用途、文档骨架、适用对象",
  "question": "你想基于它做份什么新文档？"
}
```

Create `backend/app/modules/docgen/prompts/change_plan.md`:

```markdown
你是火炬电子研发知识库里的“新文档生成”助手。

任务：根据用户的新文档需求、蓝本文档概括、检索命中的节点原文，生成改动清单。清单只描述需要改的地方，不生成最终文档。

严格规则：
- 只输出 JSON 对象。
- 每条改动必须有 location、old_text、new_text、source_type、reason、source_handles。
- source_type 只能是 "user" 或 "ai"。
- 用户明确说出的变化标为 "user"。
- 你根据上下文推理、计算、联动得到的变化标为 "ai"。
- 如果输入里包含 current_plan，输出必须是调整后的完整清单，不要只输出增量 delta；后端会用本次 items 覆盖当前清单。
- 如果某个新值需要计算，你自己决定业务公式，但必须额外输出 calculation_expression，只包含纯算术表达式，例如 "220 * (3.2 * 1.6) / (2.0 * 1.25)"。
- calculation_expression 不要包含单位、中文、函数名或变量名。
- old_text 必须来自提供的节点原文，尽量保持精确可替换。
- 如果无法确定旧值，不要编造该条改动。
- AI 推理值必须在 reason 中说明需要人工核对。

JSON schema:
{
  "items": [
    {
      "location": "章节或附表位置",
      "old_text": "蓝本中的原文片段",
      "new_text": "建议替换的新片段",
      "source_type": "user",
      "reason": "为什么这样改",
      "source_handles": ["doc_id:node_id"],
      "calculation_expression": "",
      "confidence": 0.8
    }
  ],
  "warnings": []
}
```

- [ ] **Step 4: Implement agent shell**

Create `backend/app/modules/docgen/agent.py`:

```python
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import get_settings

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def build_docgen_model(timeout: int = 180) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.litellm_api_key.get_secret_value(),  # type: ignore[arg-type]
        base_url=settings.litellm_base_url,
        temperature=0,
        timeout=timeout,
        max_retries=2,
    )


def extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM did not return a valid JSON object") from exc
    if not isinstance(data, dict):
        raise ValueError("LLM JSON must be an object")
    return data


class DocgenAgent:
    def __init__(self, model: ChatOpenAI | None = None) -> None:
        self.model = model or build_docgen_model()

    def summarize_blueprint(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.model.invoke(
            [
                SystemMessage(content=load_prompt("blueprint_summary.md")),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        return extract_json_object(str(result.content))

    def create_change_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.model.invoke(
            [
                SystemMessage(content=load_prompt("change_plan.md")),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        return extract_json_object(str(result.content))
```

- [ ] **Step 5: Run agent tests**

Run:

```powershell
uv run pytest tests/unit/docgen/test_agent.py -v
```

Expected result:

```text
passed
```

### Task 7: Implement Safe Arithmetic Calculator Tool

**Files:**

- Create: `backend/app/modules/docgen/calculator.py`
- Test: `backend/tests/unit/docgen/test_calculator.py`

- [ ] **Step 1: Write tests**

Create `backend/tests/unit/docgen/test_calculator.py`:

```python
from app.modules.docgen.calculator import calculate_parameter_note


def test_calculator_evaluates_ai_chosen_expression():
    result = calculate_parameter_note("220 * (3.2 * 1.6) / (2.0 * 1.25)")

    assert result["requires_review"] is True
    assert result["result"] == "450.56"
    assert "人工核对" in result["note"]


def test_calculator_rejects_unsafe_expression():
    result = calculate_parameter_note("__import__('os').system('dir')")

    assert result["requires_review"] is True
    assert result["result"] == ""
    assert "无法计算" in result["note"]


def test_calculator_rejects_oversized_power():
    result = calculate_parameter_note("9 ** 999999")

    assert result["requires_review"] is True
    assert result["result"] == ""
    assert "无法计算" in result["note"]
```

- [ ] **Step 2: Implement calculator**

Create `backend/app/modules/docgen/calculator.py`.

Important behavior:

- The tool accepts an arithmetic expression chosen by the AI.
- It supports numbers, parentheses, `+`, `-`, `*`, `/`, `%`, and `**`.
- It does not support names, function calls, attribute access, imports, or units inside the expression.
- It never decides business formulas.
- It always returns `requires_review=true`.

```python
from __future__ import annotations

import ast
import operator
from decimal import Decimal, DecimalException

MAX_EXPRESSION_LENGTH = 200
MAX_AST_NODES = 40
MAX_ABS_VALUE = Decimal("1e12")
MAX_POWER_EXPONENT = Decimal("12")

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POWER_EXPONENT:
            raise ValueError("power exponent too large")
        value = _OPS[type(node.op)](left, right)
        if abs(value) > MAX_ABS_VALUE:
            raise ValueError("calculation result too large")
        return value
    raise ValueError("unsupported expression")


def _format_decimal(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.000001")).normalize()
    return format(rounded, "f")


def calculate_parameter_note(user_input: str) -> dict:
    try:
        if len(user_input) > MAX_EXPRESSION_LENGTH:
            raise ValueError("expression too long")
        parsed = ast.parse(user_input, mode="eval")
        if sum(1 for _ in ast.walk(parsed)) > MAX_AST_NODES:
            raise ValueError("expression too complex")
        value = _eval(parsed)
        result = _format_decimal(value)
        note = "计算器仅计算 AI 给出的算术表达式，不代表业务公式已审核；结果必须人工核对。"
    except (SyntaxError, ValueError, ZeroDivisionError, DecimalException, OverflowError):
        result = ""
        note = "无法计算该表达式；请人工核对。"
    return {
        "input": user_input,
        "result": result,
        "note": note,
        "requires_review": True,
    }
```

- [ ] **Step 3: Run tests**

Run:

```powershell
uv run pytest tests/unit/docgen/test_calculator.py -v
```

Expected result:

```text
passed
```

### Task 8: Implement Docgen Service

**Files:**

- Create: `backend/app/modules/docgen/service.py`
- Test: `backend/tests/unit/docgen/test_service.py`

- [ ] **Step 1: Write service tests**

Create `backend/tests/unit/docgen/test_service.py`:

```python
import json
from datetime import UTC, datetime

import pytest

from app.modules.docgen import service, storage
from app.modules.docgen.schemas import DocgenStatus


class FakeStore:
    def __init__(self):
        self.read_calls = []

    def list_catalog(self, kbs=None):
        return [
            {
                "doc_id": "doc_a",
                "doc_name": "工艺文件",
                "doc_description": "工艺规程",
                "kb": "default",
            }
        ]

    def get_outline(self, doc_id):
        if doc_id != "doc_a":
            return {"error": "unknown doc"}
        return {
            "doc": "doc_a",
            "name": "工艺文件",
            "nodes": [
                {
                    "id": "doc_a:0001",
                    "title": "1 适用范围",
                    "summary": "0805 适用范围",
                    "lines": "1-3",
                    "has_children": False,
                }
            ],
        }

    def open_node(self, node_id):
        return {"node": node_id, "title": "1 适用范围", "children": []}

    def search_nodes(self, query, top_k=8, kbs=None):
        return [{"id": "doc_a:0001", "title": "1 适用范围", "snippet": "0805"}]

    def read_node(self, node_id):
        self.read_calls.append(node_id)
        return {
            "id": node_id,
            "title": "1 适用范围",
            "text": "0805 产品",
            "cite": {"handle": node_id, "doc_id": "doc_a", "doc": "工艺文件", "section": "1 适用范围"},
            "path": "工艺文件 > 1 适用范围",
        }


class FakeAgent:
    def summarize_blueprint(self, payload):
        assert "outline" in payload
        assert "0805 产品" not in json.dumps(payload, ensure_ascii=False)
        return {"summary": "蓝本摘要", "question": "你想基于它做份什么新文档？"}

    def create_change_plan(self, payload):
        assert "0805 产品" in json.dumps(payload, ensure_ascii=False)
        return {
            "items": [
                {
                    "location": "第 1 章",
                    "old_text": "0805",
                    "new_text": "1206",
                    "source_type": "user",
                    "reason": "用户指定",
                    "source_handles": ["doc_a:0001"],
                    "confidence": 0.9,
                }
            ],
            "warnings": [],
        }


class FakeParsedDocument:
    doc_name = "工艺文件"
    markdown = "# 工艺\n\n0805 产品"


def _patch_storage_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": tmp_path})(),
    )


def _make_doc(tmp_path):
    md = tmp_path / "md" / "default" / "工艺.md"
    md.parent.mkdir(parents=True)
    md.write_text("# 工艺\n\n0805 产品", encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "doc_a.json").write_text(
        json.dumps(
            {
                "id": "doc_a",
                "doc_name": "工艺文件",
                "doc_description": "工艺规程",
                "kb": "default",
                "path": str(md),
                "line_count": 3,
                "structure": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_create_session_uses_outline_not_full_text(tmp_path, monkeypatch):
    _make_doc(tmp_path)
    fake_store = FakeStore()
    monkeypatch.setattr(service, "get_tree_store", lambda: fake_store)
    monkeypatch.setattr(service, "DocgenAgent", lambda: FakeAgent())
    monkeypatch.setattr(service, "read_parsed_document", lambda doc_id: FakeParsedDocument())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)

    result = service.create_session("doc_a")

    assert result.status == DocgenStatus.waiting_for_requirement
    assert result.summary == "蓝本摘要"
    assert fake_store.read_calls == []


def test_create_plan_reads_only_retrieved_nodes(tmp_path, monkeypatch):
    _make_doc(tmp_path)
    fake_store = FakeStore()
    monkeypatch.setattr(service, "get_tree_store", lambda: fake_store)
    monkeypatch.setattr(service, "DocgenAgent", lambda: FakeAgent())
    monkeypatch.setattr(service, "read_parsed_document", lambda doc_id: FakeParsedDocument())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)
    session = service.create_session("doc_a")

    result = service.create_plan(session.session_id, "改成 1206")

    assert result.status == DocgenStatus.plan_ready
    assert result.plan[0].id == "chg_001"
    assert result.plan[0].source_label == "你指定"
    assert fake_store.read_calls == ["doc_a:0001"]


def test_render_draft_uses_only_selected_items(tmp_path, monkeypatch):
    _make_doc(tmp_path)
    fake_store = FakeStore()
    monkeypatch.setattr(service, "get_tree_store", lambda: fake_store)
    monkeypatch.setattr(service, "DocgenAgent", lambda: FakeAgent())
    monkeypatch.setattr(service, "read_parsed_document", lambda doc_id: FakeParsedDocument())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)
    session = service.create_session("doc_a")
    service.create_plan(session.session_id, "改成 1206")
    service.patch_plan(session.session_id, [{"id": "chg_001", "selected": False}])

    draft = service.create_draft(session.session_id, accepted_item_ids=None)

    assert "0805 产品" in draft.markdown
    assert draft.applied_item_ids == []


def test_create_plan_filters_hits_to_selected_blueprint(tmp_path, monkeypatch):
    class MixedStore(FakeStore):
        def search_nodes(self, query, top_k=8, kbs=None):
            return [
                {"id": "doc_b:0001", "cite": {"doc_id": "doc_b"}},
                {"id": "doc_a:0001", "cite": {"doc_id": "doc_a"}},
            ]

    _make_doc(tmp_path)
    fake_store = MixedStore()
    monkeypatch.setattr(service, "get_tree_store", lambda: fake_store)
    monkeypatch.setattr(service, "DocgenAgent", lambda: FakeAgent())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)
    session = service.create_session("doc_a")

    service.create_plan(session.session_id, "改成 1206")

    assert fake_store.read_calls == ["doc_a:0001"]


def test_create_plan_search_error_is_service_unavailable(tmp_path, monkeypatch):
    class BrokenSearchStore(FakeStore):
        def search_nodes(self, query, top_k=8, kbs=None):
            return {"error": "检索索引未构建或未加载（请先入库 ingest）"}

    _make_doc(tmp_path)
    monkeypatch.setattr(service, "get_tree_store", lambda: BrokenSearchStore())
    monkeypatch.setattr(service, "DocgenAgent", lambda: FakeAgent())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)
    session = service.create_session("doc_a")

    with pytest.raises(service.DocgenServiceUnavailable):
        service.create_plan(session.session_id, "改成 1206")


def test_create_plan_rejects_llm_source_handle_outside_blueprint(tmp_path, monkeypatch):
    class BadAgent(FakeAgent):
        def create_change_plan(self, payload):
            return {
                "items": [{
                    "location": "第 1 章",
                    "old_text": "0805",
                    "new_text": "1206",
                    "source_type": "user",
                    "reason": "用户指定",
                    "source_handles": ["doc_b:0001"],
                    "confidence": 0.9,
                }],
                "warnings": [],
            }

    _make_doc(tmp_path)
    monkeypatch.setattr(service, "get_tree_store", lambda: FakeStore())
    monkeypatch.setattr(service, "DocgenAgent", lambda: BadAgent())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)
    session = service.create_session("doc_a")

    with pytest.raises(service.DocgenUpstreamError):
        service.create_plan(session.session_id, "改成 1206")


def test_create_plan_rejects_invalid_llm_schema_without_overwriting(tmp_path, monkeypatch):
    class BadAgent(FakeAgent):
        def create_change_plan(self, payload):
            return {"items": "bad"}

    _make_doc(tmp_path)
    monkeypatch.setattr(service, "get_tree_store", lambda: FakeStore())
    monkeypatch.setattr(service, "DocgenAgent", lambda: BadAgent())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)
    session = service.create_session("doc_a")

    with pytest.raises(service.DocgenUpstreamError):
        service.create_plan(session.session_id, "改成 1206")

    assert storage.load_session(session.session_id).plan == []


def test_create_plan_marks_calculation_mismatch_unselected(tmp_path, monkeypatch):
    class CalcMismatchAgent(FakeAgent):
        def create_change_plan(self, payload):
            return {
                "items": [{
                    "location": "第 1 章",
                    "old_text": "0805",
                    "new_text": "1206 470 pF",
                    "source_type": "ai",
                    "reason": "AI 按用户要求推算，需人工核对。",
                    "source_handles": ["doc_a:0001"],
                    "calculation_expression": "220 * (3.2 * 1.6) / (2.0 * 1.25)",
                    "confidence": 0.5,
                }],
                "warnings": [],
            }

    _make_doc(tmp_path)
    monkeypatch.setattr(service, "get_tree_store", lambda: FakeStore())
    monkeypatch.setattr(service, "DocgenAgent", lambda: CalcMismatchAgent())
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 7, 2, tzinfo=UTC))
    _patch_storage_settings(monkeypatch, tmp_path)
    session = service.create_session("doc_a")

    result = service.create_plan(session.session_id, "重算电容")

    assert result.plan[0].selected is False
    assert "不一致" in result.plan[0].warning


def test_create_session_unknown_doc_raises_not_found(monkeypatch):
    monkeypatch.setattr(service, "get_tree_store", lambda: FakeStore())

    with pytest.raises(service.DocgenNotFound):
        service.create_session("missing")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/docgen/test_service.py -v
```

Expected result:

```text
ImportError
```

- [ ] **Step 3: Implement service**

Create `backend/app/modules/docgen/service.py` with these public functions and exceptions:

```python
from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.core.retrieval.document_reader import read_parsed_document
from app.core.retrieval.store_provider import get_tree_store
from app.modules.docgen import storage
from app.modules.docgen.agent import DocgenAgent
from app.modules.docgen.calculator import calculate_parameter_note
from app.modules.docgen.renderer import render_draft
from app.modules.docgen.schemas import (
    BlueprintItem,
    BlueprintListResponse,
    BlueprintSummaryLLMResponse,
    ChangePlanLLMResponse,
    ChangePlanItem,
    ChangeSourceType,
    CreateSessionResponse,
    DocgenSessionRecord,
    DocgenStatus,
    DraftResponse,
    OutlineSummaryItem,
    PlanResponse,
)


class DocgenNotFound(Exception):
    pass


class DocgenBadRequest(Exception):
    pass


class DocgenUpstreamError(Exception):
    pass


class DocgenServiceUnavailable(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _new_session_id() -> str:
    return f"b1_{_now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(6)}"


def _count_outline_nodes(
    store,
    nodes: list[dict],
    depth: int = 1,
    max_depth: int | None = 3,
) -> tuple[int, list[OutlineSummaryItem]]:
    total = 0
    outline: list[OutlineSummaryItem] = []
    for node in nodes:
        total += 1
        outline.append(
            OutlineSummaryItem(
                node_id=node.get("id", ""),
                title=node.get("title", ""),
                summary=node.get("summary", ""),
                depth=depth,
            )
        )
        if node.get("has_children") and (max_depth is None or depth < max_depth):
            opened = store.open_node(node.get("id", ""))
            children = opened.get("children", []) if not opened.get("error") else []
            child_total, child_outline = _count_outline_nodes(
                store,
                children,
                depth + 1,
                max_depth,
            )
            total += child_total
            outline.extend(child_outline)
    return total, outline


def _blueprint_from_catalog_item(store, item: dict) -> BlueprintItem:
    doc_id = item.get("doc_id", "")
    outline = store.get_outline(doc_id)
    top_nodes = outline.get("nodes", []) if not outline.get("error") else []
    node_count, _ = _count_outline_nodes(store, top_nodes, max_depth=None)
    return BlueprintItem(
        doc_id=doc_id,
        doc_name=item.get("doc_name", ""),
        doc_description=item.get("doc_description", ""),
        kb=item.get("kb", "default"),
        chapter_count=len(top_nodes),
        node_count=node_count,
    )


def list_blueprints(kbs: list[str] | None = None) -> BlueprintListResponse:
    store = get_tree_store()
    items = []
    for item in store.list_catalog(kbs=kbs):
        items.append(_blueprint_from_catalog_item(store, item))
    return BlueprintListResponse(items=items)


def create_session(doc_id: str) -> CreateSessionResponse:
    store = get_tree_store()
    catalog_item = next((it for it in store.list_catalog() if it.get("doc_id") == doc_id), None)
    if catalog_item is None:
        raise DocgenNotFound(f"unknown doc_id: {doc_id}")
    outline_raw = store.get_outline(doc_id)
    if outline_raw.get("error"):
        raise DocgenNotFound(outline_raw["error"])

    blueprint = _blueprint_from_catalog_item(store, catalog_item)
    _, outline = _count_outline_nodes(store, outline_raw.get("nodes", []))
    payload = {
        "blueprint": blueprint.model_dump(mode="json"),
        "outline": [item.model_dump(mode="json") for item in outline],
    }
    try:
        summary_data = BlueprintSummaryLLMResponse.model_validate(
            DocgenAgent().summarize_blueprint(payload)
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise DocgenUpstreamError(str(exc)) from exc

    session_id = _new_session_id()
    now = _now()
    record = DocgenSessionRecord(
        session_id=session_id,
        doc_id=doc_id,
        status=DocgenStatus.waiting_for_requirement,
        blueprint=blueprint,
        summary=summary_data.summary,
        outline=outline,
        question=summary_data.question,
        created_at=now,
        updated_at=now,
    )
    storage.save_session(record)
    return CreateSessionResponse(
        session_id=session_id,
        doc_id=doc_id,
        status=record.status,
        blueprint=record.blueprint,
        summary=record.summary,
        outline=record.outline,
        question=record.question,
    )
```

Continue the same file with plan/draft functions:

```python
def _load_record(session_id: str) -> DocgenSessionRecord:
    try:
        record = storage.load_session(session_id)
    except ValueError as exc:
        raise DocgenNotFound("unknown session") from exc
    if record is None:
        raise DocgenNotFound("unknown session")
    return record


def _belongs_to_doc(node_id: str, doc_id: str) -> bool:
    return node_id.startswith(f"{doc_id}:")


def _candidate_node_ids(
    store,
    *,
    message: str,
    doc_id: str,
    kb: str,
) -> list[str]:
    hits = store.search_nodes(message, top_k=8, kbs=[kb])
    if isinstance(hits, dict) and hits.get("error"):
        raise DocgenServiceUnavailable(hits["error"])
    node_ids: list[str] = []
    for hit in hits:
        hit_id = hit.get("id", "")
        hit_doc_id = (hit.get("cite") or {}).get("doc_id", "")
        if hit_doc_id and hit_doc_id != doc_id:
            continue
        section = hit.get("section") or {}
        span = section.get("span") or []
        if span:
            node_ids.extend([node_id for node_id in span if _belongs_to_doc(node_id, doc_id)])
        elif _belongs_to_doc(hit_id, doc_id):
            node_ids.append(hit_id)
    return list(dict.fromkeys([node_id for node_id in node_ids if node_id]))


def _first_decimal(text: str) -> Decimal | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _calculation_matches_text(new_text: str, result: str) -> bool:
    if not result:
        return True
    proposed = _first_decimal(new_text)
    if proposed is None:
        return True
    try:
        expected = Decimal(result)
    except InvalidOperation:
        return False
    return abs(proposed - expected) <= Decimal("0.000001")


def create_plan(session_id: str, message: str) -> PlanResponse:
    record = _load_record(session_id)
    store = get_tree_store()
    node_payloads = []
    for node_id in _candidate_node_ids(
        store,
        message=message,
        doc_id=record.doc_id,
        kb=record.blueprint.kb,
    ):
        node = store.read_node(node_id)
        if node.get("error"):
            continue
        node_payloads.append(
            {
                "id": node.get("id", node_id),
                "title": node.get("title", ""),
                "path": node.get("path", ""),
                "text": node.get("text", ""),
                "cite": node.get("cite", {}),
            }
        )
    payload = {
        "blueprint": record.blueprint.model_dump(mode="json"),
        "summary": record.summary,
        "outline": [item.model_dump(mode="json") for item in record.outline],
        "message": message,
        "previous_messages": record.messages,
        "current_plan": [item.model_dump(mode="json") for item in record.plan],
        "nodes": node_payloads,
    }
    try:
        plan_data = ChangePlanLLMResponse.model_validate(
            DocgenAgent().create_change_plan(payload)
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise DocgenUpstreamError(str(exc)) from exc

    node_text_by_id = {str(node["id"]): str(node.get("text", "")) for node in node_payloads}
    allowed_node_ids = set(node_text_by_id)

    try:
        plan = []
        for idx, item in enumerate(plan_data.items, start=1):
            handles = [handle for handle in item.source_handles if handle in allowed_node_ids]
            if not handles:
                raise ValueError("plan item source handle was not read from blueprint")
            node_id = handles[0]
            if item.old_text not in node_text_by_id[node_id]:
                raise ValueError("plan item old_text is not present in source node")
            calc_expr = item.calculation_expression.strip()
            calc = calculate_parameter_note(calc_expr) if calc_expr else None
            calc_result = (calc or {}).get("result", "")
            selected = True
            warning = ""
            if calc_expr and not _calculation_matches_text(item.new_text, calc_result):
                selected = False
                warning = "AI 新值与计算器结果不一致，未自动应用；请人工核对。"
            plan.append(
                ChangePlanItem(
                    id=f"chg_{idx:03d}",
                    node_id=node_id,
                    location=item.location,
                    old_text=item.old_text,
                    new_text=item.new_text,
                    source_type=item.source_type,
                    reason=item.reason,
                    source_handles=handles,
                    calculation_expression=calc_expr,
                    calculation_result=calc_result,
                    calculation_note=(calc or {}).get("note", ""),
                    selected=selected,
                    confidence=item.confidence,
                    warning=warning,
                )
            )
    except (TypeError, ValueError, ValidationError) as exc:
        raise DocgenUpstreamError(f"invalid change plan: {exc}") from exc

    def apply_plan(latest: DocgenSessionRecord) -> DocgenSessionRecord:
        latest.messages.append({
            "role": "user",
            "content": message,
            "created_at": _now().isoformat(),
        })
        latest.plan = plan
        latest.status = DocgenStatus.plan_ready
        latest.warnings = list(plan_data.warnings)
        latest.updated_at = _now()
        return latest

    updated = storage.update_session(session_id, apply_plan)
    if updated is None:
        raise DocgenNotFound("unknown session")
    return PlanResponse(
        session_id=updated.session_id,
        status=updated.status,
        plan=updated.plan,
        warnings=updated.warnings,
    )


def patch_plan(session_id: str, selections: list[dict]) -> PlanResponse:
    record = _load_record(session_id)
    known = {item.id: item for item in record.plan}
    invalid = [item["id"] for item in selections if item["id"] not in known]
    if invalid:
        raise DocgenBadRequest(f"unknown plan item ids: {', '.join(invalid)}")

    def apply_selection(latest: DocgenSessionRecord) -> DocgenSessionRecord:
        latest_known = {item.id: item for item in latest.plan}
        for selection in selections:
            latest_known[selection["id"]].selected = bool(selection["selected"])
        latest.updated_at = _now()
        return latest

    updated = storage.update_session(session_id, apply_selection)
    if updated is None:
        raise DocgenNotFound("unknown session")
    return PlanResponse(
        session_id=updated.session_id,
        status=updated.status,
        plan=updated.plan,
        warnings=updated.warnings,
    )


def create_draft(session_id: str, accepted_item_ids: list[str] | None) -> DraftResponse:
    record = _load_record(session_id)
    parsed = read_parsed_document(record.doc_id)
    if parsed is None:
        raise DocgenNotFound(f"unknown doc_id: {record.doc_id}")
    draft = render_draft(
        title=parsed.doc_name or record.blueprint.doc_name,
        markdown=parsed.markdown,
        plan=record.plan,
        accepted_item_ids=accepted_item_ids,
    )
    draft.session_id = record.session_id
    def apply_draft(latest: DocgenSessionRecord) -> DocgenSessionRecord:
        latest.draft = draft
        latest.status = DocgenStatus.draft_ready
        latest.updated_at = _now()
        return latest

    updated = storage.update_session(session_id, apply_draft)
    if updated is None:
        raise DocgenNotFound("unknown session")
    return draft


def get_session(session_id: str) -> DocgenSessionRecord:
    record = _load_record(session_id)
    return record
```

- [ ] **Step 4: Run service tests**

Run:

```powershell
uv run pytest tests/unit/docgen/test_service.py -v
```

Expected result:

```text
passed
```

### Task 9: Implement Router And Register It

**Files:**

- Create: `backend/app/modules/docgen/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_docgen_router.py`

- [ ] **Step 1: Write integration tests**

Create `backend/tests/integration/test_docgen_router.py`:

```python
from fastapi.testclient import TestClient

from app.main import app
from app.modules.docgen import router as docgen_router
from app.modules.docgen.schemas import (
    BlueprintItem,
    BlueprintListResponse,
    CreateSessionResponse,
    DocgenStatus,
    DraftResponse,
    PlanResponse,
)


def test_blueprints_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "list_blueprints",
        lambda kbs=None: BlueprintListResponse(
            items=[BlueprintItem(doc_id="doc_a", doc_name="工艺文件")]
        ),
    )

    res = TestClient(app).get("/docgen/blueprints")

    assert res.status_code == 200
    assert res.json()["items"][0]["doc_id"] == "doc_a"


def test_create_session_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_session",
        lambda doc_id: CreateSessionResponse(
            session_id="b1_1",
            doc_id=doc_id,
            status=DocgenStatus.waiting_for_requirement,
            blueprint=BlueprintItem(doc_id=doc_id, doc_name="工艺文件"),
            summary="蓝本摘要",
            outline=[],
            question="你想基于它做份什么新文档？",
        ),
    )

    res = TestClient(app).post("/docgen/sessions", json={"doc_id": "doc_a"})

    assert res.status_code == 200
    assert res.json()["summary"] == "蓝本摘要"


def test_plan_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_plan",
        lambda session_id, message: PlanResponse(
            session_id=session_id,
            status=DocgenStatus.plan_ready,
            plan=[],
            warnings=[],
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/plan", json={"message": "改成 1206"})

    assert res.status_code == 200
    assert res.json()["status"] == "plan_ready"


def test_draft_endpoint(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_draft",
        lambda session_id, accepted_item_ids: DraftResponse(
            session_id=session_id,
            status=DocgenStatus.draft_ready,
            title="工艺文件（初稿）",
            markdown="正文",
            copy_text="正文",
            highlighted_markdown="正文",
            applied_item_ids=[],
            skipped_item_ids=[],
            unresolved_item_ids=[],
            warnings=["初稿仅供参考，必须经研发人员审核。"],
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/draft", json={})

    assert res.status_code == 200
    assert res.json()["status"] == "draft_ready"


def test_router_maps_not_found(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_session",
        lambda doc_id: (_ for _ in ()).throw(docgen_router.service.DocgenNotFound("missing")),
    )

    res = TestClient(app).post("/docgen/sessions", json={"doc_id": "missing"})

    assert res.status_code == 404


def test_router_maps_bad_request(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "patch_plan",
        lambda session_id, items: (_ for _ in ()).throw(
            docgen_router.service.DocgenBadRequest("bad")
        ),
    )

    res = TestClient(app).patch(
        "/docgen/sessions/b1_1/plan",
        json={"items": [{"id": "missing", "selected": True}]},
    )

    assert res.status_code == 400


def test_router_maps_upstream_error(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_plan",
        lambda session_id, message: (_ for _ in ()).throw(
            docgen_router.service.DocgenUpstreamError("bad llm")
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/plan", json={"message": "改"})

    assert res.status_code == 502


def test_router_maps_service_unavailable(monkeypatch):
    monkeypatch.setattr(
        docgen_router.service,
        "create_plan",
        lambda session_id, message: (_ for _ in ()).throw(
            docgen_router.service.DocgenServiceUnavailable("index missing")
        ),
    )

    res = TestClient(app).post("/docgen/sessions/b1_1/plan", json={"message": "改"})

    assert res.status_code == 503
```

- [ ] **Step 2: Run tests and verify route missing**

Run:

```powershell
uv run pytest tests/integration/test_docgen_router.py -v
```

Expected result:

```text
ImportError: cannot import name 'router' from 'app.modules.docgen'
```

- [ ] **Step 3: Implement router**

Create `backend/app/modules/docgen/router.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.modules.docgen import service
from app.modules.docgen.schemas import (
    BlueprintListResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DraftRequest,
    DraftResponse,
    PatchPlanRequest,
    PlanRequest,
    PlanResponse,
)

router = APIRouter(prefix="/docgen", tags=["docgen"])


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, service.DocgenNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.DocgenBadRequest):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, service.DocgenUpstreamError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, service.DocgenServiceUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/blueprints", response_model=BlueprintListResponse)
def list_blueprints(kbs: str = "") -> BlueprintListResponse:
    parsed_kbs = [s.strip() for s in kbs.split(",") if s.strip()] or None
    try:
        return service.list_blueprints(kbs=parsed_kbs)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/sessions", response_model=CreateSessionResponse)
def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
    try:
        return service.create_session(req.doc_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    try:
        return service.get_session(session_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/sessions/{session_id}/plan", response_model=PlanResponse)
def create_plan(session_id: str, req: PlanRequest) -> PlanResponse:
    try:
        return service.create_plan(session_id, req.message)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.patch("/sessions/{session_id}/plan", response_model=PlanResponse)
def patch_plan(session_id: str, req: PatchPlanRequest) -> PlanResponse:
    try:
        return service.patch_plan(
            session_id,
            [item.model_dump(mode="json") for item in req.items],
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/sessions/{session_id}/draft", response_model=DraftResponse)
def create_draft(session_id: str, req: DraftRequest) -> DraftResponse:
    try:
        return service.create_draft(session_id, req.accepted_item_ids)
    except Exception as exc:
        raise _map_error(exc) from exc
```

- [ ] **Step 4: Register router in `main.py`**

Modify `backend/app/main.py` near existing router imports:

```python
from app.modules.chat.router import router as chat_router  # noqa: E402
from app.modules.docgen.router import router as docgen_router  # noqa: E402
from app.modules.ingest.router import router as ingest_router  # noqa: E402

app.include_router(ingest_router)
app.include_router(chat_router)
app.include_router(docgen_router)
```

- [ ] **Step 5: Run router tests**

Run:

```powershell
uv run pytest tests/integration/test_docgen_router.py -v
```

Expected result:

```text
passed
```

### Task 10: Integrate B1 Page Into Existing Frontend

**Files:**

- Modify: `product/design/v0-mockup-v2.html`

Key constraints for this task:

- Do not render plan cards into the chat stream with repeated ids.
- Use one fixed `#docgen-plan-slot` container; every new plan replaces the previous plan.
- Keep all selectors scoped to `#docgen-plan-slot` when reading checkboxes.
- Optimistically update local checkbox state before PATCH to avoid generate/PATCH races.
- Copy `DraftResponse.copy_text`, not `markdown` or preview HTML.
- Use request sequence guards so stale API responses cannot overwrite a newer session.

- [ ] **Step 1: Add nav item**

In the “主操作” block after `发起新对话`, add:

```html
<a class="nav-item" data-view="doc-generate" title="基于历史框架生成新文档">
  <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" d="M12 20h9"/>
    <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4 12.5-12.5z"/>
  </svg>
  <span class="nav-label">生成文档</span>
</a>
```

- [ ] **Step 2: Add B1 view pane**

Add a new section after the chat section and before knowledge-base management sections.

```html
<section data-view="doc-generate" class="view-pane hidden flex-1 flex gap-4 animate-enter min-h-0">
  <div class="flex-1 flex flex-col min-w-0 min-h-0">
    <header class="mb-4 flex items-center justify-between gap-3 flex-wrap">
      <div class="min-w-0">
        <h1 class="text-xl font-semibold text-slate-800">生成文档</h1>
        <p class="text-sm text-slate-500 mt-1">基于历史文档框架生成新文档初稿</p>
      </div>
      <button id="docgen-reset" class="btn-ghost hidden">重新选择蓝本</button>
    </header>

    <div id="docgen-pick" class="flex-1 overflow-y-auto custom-scrollbar min-h-0">
      <div class="glass-soft rounded-3xl p-6 mb-4">
        <p class="text-sm text-slate-600">选择一份最接近的新产品历史文档作蓝本。系统只生成初稿，必须经研发人员审核。</p>
        <div class="mt-4 flex justify-end">
          <button id="docgen-refresh" class="btn-ghost">刷新蓝本</button>
        </div>
      </div>
      <div id="docgen-blueprints" class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4"></div>
    </div>

    <div id="docgen-work" class="hidden flex-1 h-full min-h-0 overflow-y-auto xl:overflow-hidden custom-scrollbar grid grid-cols-1 xl:grid-cols-[minmax(360px,0.95fr)_minmax(460px,1.2fr)] gap-4">
      <div class="glass-soft rounded-3xl flex flex-col min-h-[520px] xl:h-full xl:min-h-0 xl:overflow-hidden">
        <div class="px-5 py-4 border-b border-white/70 flex items-center justify-between gap-2">
          <div class="min-w-0">
            <p id="docgen-blueprint-title" class="text-sm font-semibold text-slate-800 truncate">蓝本</p>
            <p id="docgen-status" class="text-xs text-slate-400 mt-0.5">等待需求</p>
          </div>
          <span class="chip chip-purple">plan 模式</span>
        </div>
        <div id="docgen-stream" class="min-h-0 overflow-y-auto custom-scrollbar p-4 space-y-3" style="max-height: 34%;"></div>
        <div id="docgen-plan-slot" class="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-4"></div>
        <div class="p-4 border-t border-white/70">
          <textarea id="docgen-input" class="input-base w-full resize-none" rows="3" placeholder="例如：做 1206 规格的新产品，厚度改成 15±2μm，其它沿用"></textarea>
          <div class="flex items-center justify-between gap-2 mt-3 flex-wrap">
            <p class="text-[11px] text-slate-400">AI 会先给改动清单，确认后才生成初稿。</p>
            <button id="docgen-send" class="btn-primary">发送</button>
          </div>
        </div>
      </div>

      <div class="glass-soft rounded-3xl flex flex-col min-h-[520px] xl:h-full xl:min-h-0 xl:overflow-hidden">
        <div class="px-5 py-4 border-b border-white/70 flex items-center justify-between gap-2 flex-wrap">
          <div class="min-w-0">
            <p id="docgen-draft-title" class="text-sm font-semibold text-slate-800 truncate">蓝本预览</p>
            <p id="docgen-draft-badge" class="text-xs text-slate-400 mt-0.5">未生成</p>
          </div>
          <div class="flex items-center gap-2 flex-wrap">
            <button id="docgen-next-highlight" class="btn-ghost hidden">下一个高亮</button>
            <button id="docgen-copy" class="btn-ghost hidden">复制全文</button>
          </div>
        </div>
        <div id="docgen-draft" class="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-5 md text-sm leading-relaxed"></div>
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Add CSS for plan, highlights, and current highlight**

Add near existing markdown/chat styles:

```css
.docgen-plan-card { border:1px solid rgba(139,92,246,.18); background:rgba(255,255,255,.78); border-radius:18px; padding:14px; }
.docgen-plan-item { display:flex; gap:10px; align-items:flex-start; padding:10px 0; border-top:1px solid rgba(148,163,184,.18); }
.docgen-plan-item:first-child { border-top:0; }
.docgen-change-user { background:#fef3c7; color:#78350f; border-radius:4px; padding:0 2px; }
.docgen-change-ai { background:#fef3c7; color:#78350f; border-radius:4px; padding:0 2px; border-bottom:1px dashed #b45309; }
.docgen-highlight-active { outline:2px solid #8b79d4; box-shadow:0 0 0 3px rgba(139,121,212,.18); }
.docgen-unchanged-badge { display:inline-flex; margin-left:8px; padding:1px 6px; border-radius:999px; font-size:10px; color:#64748b; background:#f1f5f9; }
```

- [ ] **Step 4: Add safe frontend helpers**

Add after `API_BASE`:

```javascript
const docgenState = {
  sessionId: null,
  blueprint: null,
  plan: [],
  copyText: '',
  highlightIndex: -1,
  requestSeq: 0,
  patchSeq: 0,
  draftSeq: 0,
  patchPending: false
};

async function docgenFetchJson(url, options){
  const res = await fetch(url, options);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch(e) { throw new Error(`HTTP ${res.status}: ${text.slice(0, 160) || '非 JSON 响应'}`); }
  if(!res.ok || data.detail || data.error){
    throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  }
  return data;
}

function docgenSetStatus(text){
  const el = document.getElementById('docgen-status');
  if(el) el.textContent = text;
}

function docgenSetBusy(on){
  ['docgen-send','docgen-refresh','docgen-reset','docgen-copy','docgen-next-highlight'].forEach(id => {
    const el = document.getElementById(id);
    if(el) el.disabled = !!on;
  });
  document.querySelectorAll('#docgen-plan-slot [data-docgen-plan]').forEach(chk => {
    chk.disabled = !!on || docgenState.patchPending;
  });
  const gen = document.querySelector('#docgen-plan-slot [data-docgen-generate]');
  if(gen) gen.disabled = !!on || docgenState.patchPending;
}

function docgenTextMsg(role, text){
  const stream = document.getElementById('docgen-stream');
  if(!stream) return;
  const outer = document.createElement('div');
  outer.className = role === 'user' ? 'flex justify-end' : 'flex justify-start';
  const bubble = document.createElement('div');
  bubble.className = role === 'user'
    ? 'bg-accent text-white rounded-2xl px-4 py-3 text-sm max-w-[92%] shadow-sm'
    : 'bg-white/85 text-slate-700 rounded-2xl px-4 py-3 text-sm max-w-[92%] shadow-sm';
  bubble.textContent = text || '';
  outer.appendChild(bubble);
  stream.appendChild(outer);
  stream.scrollTop = stream.scrollHeight;
}

function docgenSafeHtmlMsg(localSafeHtml){
  const stream = document.getElementById('docgen-stream');
  if(!stream) return;
  const outer = document.createElement('div');
  outer.className = 'flex justify-start';
  const bubble = document.createElement('div');
  bubble.className = 'bg-white/85 text-slate-700 rounded-2xl px-4 py-3 text-sm max-w-[92%] shadow-sm';
  bubble.innerHTML = DOMPurify.sanitize(localSafeHtml, {ALLOWED_TAGS:['b','strong','span','br']});
  outer.appendChild(bubble);
  stream.appendChild(outer);
  stream.scrollTop = stream.scrollHeight;
}
```

- [ ] **Step 5: Add B1 Markdown rendering helper**

Do not feed arbitrary backend HTML directly into the page. Backend only emits `<mark class="docgen-change-user|docgen-change-ai">` in `highlighted_markdown`; the helper preserves those marks and keeps all other Markdown rendering through existing `mdToHtml()`.

```javascript
function docgenMdToHtml(text){
  const marks = [];
  const protectedText = String(text || '').replace(
    /<mark class="(docgen-change-user|docgen-change-ai)">([\s\S]*?)<\/mark>/g,
    (_, cls, body) => {
      const token = `@@DOCGEN_MARK_${marks.length}@@`;
      marks.push({cls, body});
      return token;
    }
  );
  let html = mdToHtml(protectedText);
  marks.forEach((m, i) => {
    html = html.replace(
      `@@DOCGEN_MARK_${i}@@`,
      `<mark class="${m.cls}">${escapeHtml(m.body)}</mark>`
    );
  });
  return html;
}

function docgenAddUnchangedBadges(container){
  container.querySelectorAll('h1,h2,h3,h4').forEach(h => {
    const level = Number(h.tagName.slice(1));
    let node = h.nextElementSibling;
    let sectionHasChange = !!h.querySelector('.docgen-change-user, .docgen-change-ai');
    while(node){
      if(/^H[1-4]$/.test(node.tagName) && Number(node.tagName.slice(1)) <= level) break;
      if(node.querySelector?.('.docgen-change-user, .docgen-change-ai')){
        sectionHasChange = true;
        break;
      }
      node = node.nextElementSibling;
    }
    if(sectionHasChange || h.querySelector('.docgen-unchanged-badge')) return;
    const badge = document.createElement('span');
    badge.className = 'docgen-unchanged-badge';
    badge.textContent = '原样沿用';
    h.appendChild(badge);
  });
}
```

- [ ] **Step 6: Add blueprint/session functions**

```javascript
async function loadDocgenBlueprints(){
  const box = document.getElementById('docgen-blueprints');
  if(!box) return;
  box.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">加载中…</p>';
  try{
    const data = await docgenFetchJson(API_BASE + '/docgen/blueprints');
    const items = data.items || [];
    if(!items.length){
      box.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">暂无可用蓝本文档</p>';
      return;
    }
    box.innerHTML = items.map(it => `
      <button class="glass-soft rounded-3xl p-5 text-left hover:shadow-md transition" data-docgen-blueprint="${escapeHtml(it.doc_id)}">
        <p class="text-sm font-semibold text-slate-800 truncate">${escapeHtml(it.doc_name)}</p>
        <p class="text-xs text-slate-400 mt-1 truncate">${escapeHtml(it.doc_description || '')}</p>
        <p class="text-xs text-slate-400 mt-1 truncate">所属知识库：${escapeHtml(it.kb || 'default')}</p>
        <div class="grid grid-cols-2 gap-2 text-xs text-slate-500 mt-3">
          <span>${Number(it.chapter_count || 0)} 章</span>
          <span>${Number(it.node_count || 0)} 节点</span>
        </div>
      </button>
    `).join('');
    box.querySelectorAll('[data-docgen-blueprint]').forEach(btn => {
      btn.addEventListener('click', () => startDocgenSession(btn.dataset.docgenBlueprint));
    });
  }catch(err){
    box.innerHTML = `<p class="text-sm text-red-500 py-8 text-center">加载失败：${escapeHtml(err.message)}</p>`;
  }
}

function resetDocgenDraftUi(){
  docgenState.copyText = '';
  docgenState.highlightIndex = -1;
  document.getElementById('docgen-copy')?.classList.add('hidden');
  document.getElementById('docgen-next-highlight')?.classList.add('hidden');
  document.getElementById('docgen-draft-badge').textContent = '未生成';
}

async function startDocgenSession(docId){
  const seq = ++docgenState.requestSeq;
  docgenSetBusy(true);
  resetDocgenDraftUi();
  docgenState.sessionId = null;
  docgenState.draftSeq += 1;
  docgenState.patchPending = false;
  docgenState.plan = [];
  document.getElementById('docgen-plan-slot').innerHTML = '';
  document.getElementById('docgen-stream').innerHTML = '';
  document.getElementById('docgen-pick')?.classList.add('hidden');
  document.getElementById('docgen-work')?.classList.remove('hidden');
  document.getElementById('docgen-reset')?.classList.remove('hidden');
  document.getElementById('docgen-draft').innerHTML = '<p class="text-slate-400">请稍候…</p>';
  docgenSetStatus('请稍候');
  try{
    const data = await docgenFetchJson(API_BASE + '/docgen/sessions', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:docId})
    });
    if(seq !== docgenState.requestSeq) return;
    docgenState.sessionId = data.session_id;
    docgenState.blueprint = data.blueprint;
    document.getElementById('docgen-blueprint-title').textContent = '蓝本：' + (data.blueprint?.doc_name || docId);
    document.getElementById('docgen-draft-title').textContent = data.blueprint?.doc_name || '蓝本预览';
    docgenSetStatus('等待需求');
    docgenTextMsg('assistant', `${data.summary || ''}\n\n${data.question || '你想基于它做份什么新文档？'}`);
    document.getElementById('docgen-draft').innerHTML = mdToHtml((data.outline || []).map(x => `${'#'.repeat(Math.min(x.depth || 1, 4))} ${x.title}\n\n${x.summary || ''}`).join('\n\n'));
  }catch(err){
    if(seq !== docgenState.requestSeq) return;
    docgenSetStatus('概括失败');
    docgenTextMsg('assistant', '失败：' + err.message);
  }finally{
    if(seq === docgenState.requestSeq) docgenSetBusy(false);
  }
}
```

- [ ] **Step 7: Add plan rendering without duplicate ids**

```javascript
function renderDocgenPlan(){
  const slot = document.getElementById('docgen-plan-slot');
  const items = docgenState.plan || [];
  if(!slot) return;
  slot.innerHTML = `<div class="docgen-plan-card">
    <p class="text-sm font-semibold text-slate-800 mb-2">改动清单</p>
    ${items.map(it => `
      <label class="docgen-plan-item">
        <input type="checkbox" data-docgen-plan="${escapeHtml(it.id)}" ${it.selected ? 'checked' : ''} class="mt-1">
        <span class="min-w-0">
          <span class="block text-xs font-semibold text-slate-700">${escapeHtml(it.location || '')}</span>
          <span class="block text-sm text-slate-800 mt-1">${escapeHtml(it.old_text || '')} → ${escapeHtml(it.new_text || '')}</span>
          <span class="block text-xs text-slate-400 mt-1">${escapeHtml(it.source_label || '')} · ${escapeHtml(it.reason || '')}</span>
          ${it.calculation_note ? `<span class="block text-xs text-amber-700 mt-1">${escapeHtml(it.calculation_note)}</span>` : ''}
          ${it.warning ? `<span class="block text-xs text-red-500 mt-1">${escapeHtml(it.warning)}</span>` : ''}
        </span>
      </label>
    `).join('')}
    <div class="flex justify-end gap-2 mt-3">
      <button class="btn-ghost" data-docgen-adjust>继续调整</button>
      <button class="btn-primary" data-docgen-generate>确认生成初稿</button>
    </div>
  </div>`;
  slot.querySelectorAll('[data-docgen-plan]').forEach(chk => {
    chk.addEventListener('change', () => patchDocgenPlanFromSlot(slot));
  });
  slot.querySelector('[data-docgen-adjust]')?.addEventListener('click', () => {
    document.getElementById('docgen-input')?.focus();
  });
  slot.querySelector('[data-docgen-generate]')?.addEventListener('click', generateDocgenDraft);
}

async function patchDocgenPlanFromSlot(slot){
  const patchSeq = ++docgenState.patchSeq;
  const items = Array.from(slot.querySelectorAll('[data-docgen-plan]')).map(chk => ({
    id: chk.dataset.docgenPlan,
    selected: chk.checked
  }));
  docgenState.plan = docgenState.plan.map(it => {
    const next = items.find(x => x.id === it.id);
    return next ? {...it, selected: next.selected} : it;
  });
  docgenState.patchPending = true;
  docgenSetBusy(true);
  try{
    const data = await docgenFetchJson(
      API_BASE + '/docgen/sessions/' + encodeURIComponent(docgenState.sessionId) + '/plan',
      {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({items})}
    );
    if(patchSeq === docgenState.patchSeq){
      docgenState.plan = data.plan || docgenState.plan;
    }
  }finally{
    if(patchSeq === docgenState.patchSeq){
      docgenState.patchPending = false;
      docgenSetBusy(false);
    }
  }
}
```

- [ ] **Step 8: Add requirement submit and draft generation**

```javascript
async function sendDocgenRequirement(){
  const input = document.getElementById('docgen-input');
  const msg = (input?.value || '').trim();
  if(!msg){ alert('先描述新文档要改什么'); return; }
  if(!docgenState.sessionId){ alert('请先选择蓝本'); return; }
  const seq = ++docgenState.requestSeq;
  input.value = '';
  docgenTextMsg('user', msg);
  docgenSetStatus('正在生成改动清单…');
  docgenSetBusy(true);
  try{
    const data = await docgenFetchJson(
      API_BASE + '/docgen/sessions/' + encodeURIComponent(docgenState.sessionId) + '/plan',
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:msg})}
    );
    if(seq !== docgenState.requestSeq) return;
    docgenState.plan = data.plan || [];
    docgenSetStatus('改动清单待确认');
    renderDocgenPlan();
  }catch(err){
    if(seq !== docgenState.requestSeq) return;
    docgenSetStatus('生成清单失败');
    docgenTextMsg('assistant', '失败：' + err.message);
  }finally{
    if(seq === docgenState.requestSeq) docgenSetBusy(false);
  }
}

async function generateDocgenDraft(){
  if(docgenState.patchPending) return;
  const seq = docgenState.requestSeq;
  const draftSeq = ++docgenState.draftSeq;
  const sessionId = docgenState.sessionId;
  const slot = document.getElementById('docgen-plan-slot');
  const selected = Array.from(slot?.querySelectorAll('[data-docgen-plan]') || [])
    .filter(chk => chk.checked)
    .map(chk => chk.dataset.docgenPlan);
  docgenSetStatus('正在生成初稿…');
  docgenSetBusy(true);
  try{
    const data = await docgenFetchJson(
      API_BASE + '/docgen/sessions/' + encodeURIComponent(sessionId) + '/draft',
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({accepted_item_ids:selected})}
    );
    if(seq !== docgenState.requestSeq || draftSeq !== docgenState.draftSeq) return;
    if(!data.copy_text){
      throw new Error('后端未返回 copy_text，无法保证复制纯文本');
    }
    if(!data.highlighted_markdown){
      throw new Error('后端未返回 highlighted_markdown，无法展示初稿预览');
    }
    docgenState.copyText = data.copy_text;
    document.getElementById('docgen-draft-title').textContent = data.title || '新文档初稿';
    document.getElementById('docgen-draft-badge').textContent = '初稿 · 待审核';
    document.getElementById('docgen-copy')?.classList.remove('hidden');
    document.getElementById('docgen-next-highlight')?.classList.toggle('hidden', !(Number(data.highlight_count || 0) > 0));
    const draftBox = document.getElementById('docgen-draft');
    draftBox.innerHTML = docgenMdToHtml(data.highlighted_markdown);
    docgenAddUnchangedBadges(draftBox);
    docgenState.highlightIndex = -1;
    docgenSetStatus('初稿已生成');
  }catch(err){
    if(seq !== docgenState.requestSeq || draftSeq !== docgenState.draftSeq) return;
    docgenSetStatus('生成初稿失败');
    docgenTextMsg('assistant', '失败：' + err.message);
  }finally{
    if(seq === docgenState.requestSeq && draftSeq === docgenState.draftSeq){
      docgenSetBusy(false);
    }
  }
}
```

- [ ] **Step 9: Add event bindings, copy fallback, and highlight navigation**

```javascript
document.getElementById('docgen-refresh')?.addEventListener('click', loadDocgenBlueprints);
document.getElementById('docgen-send')?.addEventListener('click', sendDocgenRequirement);
document.getElementById('docgen-input')?.addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){
    e.preventDefault();
    sendDocgenRequirement();
  }
});
document.getElementById('docgen-reset')?.addEventListener('click', () => {
  docgenState.requestSeq += 1;
  docgenState.draftSeq += 1;
  docgenState.sessionId = null;
  docgenState.blueprint = null;
  docgenState.plan = [];
  docgenState.patchPending = false;
  docgenState.copyText = '';
  docgenState.highlightIndex = -1;
  document.getElementById('docgen-work')?.classList.add('hidden');
  document.getElementById('docgen-pick')?.classList.remove('hidden');
  document.getElementById('docgen-reset')?.classList.add('hidden');
  document.getElementById('docgen-plan-slot').innerHTML = '';
  resetDocgenDraftUi();
  loadDocgenBlueprints();
});
document.getElementById('docgen-copy')?.addEventListener('click', async () => {
  try{
    await navigator.clipboard.writeText(docgenState.copyText || '');
    alert('已复制全文');
  }catch(e){
    const text = docgenState.copyText || '';
    window.prompt('自动复制失败，请手动复制以下内容：', text);
  }
});
document.getElementById('docgen-next-highlight')?.addEventListener('click', () => {
  const box = document.getElementById('docgen-draft');
  const marks = Array.from(box?.querySelectorAll('.docgen-change-user, .docgen-change-ai') || []);
  if(!marks.length) return;
  marks.forEach(m => m.classList.remove('docgen-highlight-active'));
  docgenState.highlightIndex = (docgenState.highlightIndex + 1) % marks.length;
  const current = marks[docgenState.highlightIndex];
  current.classList.add('docgen-highlight-active');
  current.scrollIntoView({block:'center', behavior:'smooth'});
  document.getElementById('docgen-next-highlight').textContent = `下一个高亮 ${docgenState.highlightIndex + 1}/${marks.length}`;
});
```

- [ ] **Step 10: Trigger blueprint loading through existing switchPage wrapper**

`v0-mockup-v2.html` already wraps `switchPage` near the bottom for page-specific loading. Add B1 loading there, not inside the base `switchPage()` function:

```javascript
if(name === 'doc-generate' && !docgenState.sessionId){
  loadDocgenBlueprints();
}
```

This avoids reloading blueprint cards while a session is already active and avoids racing with “用作蓝本生成”.

- [ ] **Step 11: Add knowledge-base list shortcut**

In `loadKbList()` document action buttons, add:

```html
<button class="btn-ghost !py-1.5 !px-3 text-xs" data-docgen-from-doc="${escapeHtml(doc.doc_id)}">生成</button>
```

After existing event bindings:

```javascript
grid.querySelectorAll('[data-docgen-from-doc]').forEach(b => b.addEventListener('click', () => {
  switchPage('doc-generate');
  startDocgenSession(b.dataset.docgenFromDoc);
}));
```

- [ ] **Step 12: Manual frontend smoke test**

Start backend:

```powershell
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

Expected behavior:

- Sidebar shows “生成文档”.
- Clicking it shows blueprint cards.
- Selecting a blueprint switches to two-column work view.
- Summary appears in left pane.
- Plan appears after sending a requirement.
- Sending a second requirement replaces the previous plan in `#docgen-plan-slot`; no duplicate generate buttons exist.
- Draft appears after confirmation.
- “下一个高亮” scrolls between changed values and visibly outlines the current one.
- Unchanged headings show UI-only “原样沿用” badges in preview.
- Copy button copies `copy_text` plain text, without highlight marks or UI-only badges.

### Task 11: Full Verification

**Files:**

- No new files.

- [ ] **Step 1: Run focused backend tests**

Run from `backend/`:

```powershell
uv run pytest tests/unit/retrieval/test_store_provider.py tests/unit/retrieval/test_document_reader.py tests/unit/docgen tests/integration/test_docgen_router.py -v
```

Expected result:

```text
passed
```

- [ ] **Step 2: Run existing affected tests**

Run:

```powershell
uv run pytest tests/unit/test_tools.py tests/unit/test_chat_sources.py tests/unit/test_treestore.py tests/unit/test_hybrid_search.py -v
```

Expected result:

```text
passed
```

- [ ] **Step 3: Run lint**

Run:

```powershell
uv run ruff check .
```

Expected result:

```text
All checks passed!
```

- [ ] **Step 4: Run type check**

Run:

```powershell
uv run pyright
```

Expected result:

```text
0 errors
```

- [ ] **Step 5: Check generated files do not pollute ingest data**

Before manual B1 test, record:

```powershell
Get-ChildItem backend\data\md -Recurse | Measure-Object
Get-ChildItem backend\data\workspace -Recurse | Measure-Object
Get-ChildItem backend\data\catalog -Recurse | Measure-Object
Get-ChildItem backend\data\indexes -Recurse | Measure-Object
```

Run B1 manually, then run the same commands.

Expected:

- Only `backend\data\docgen\sessions\*.json` changes.
- No new or modified files under `md`, `workspace`, `catalog`, or `indexes`.

- [ ] **Step 6: Check git status before commit**

Run from repository root:

```powershell
git status --short
```

Expected B1-related files:

```text
M backend/app/main.py
M backend/app/modules/chat/tools.py
A backend/app/core/retrieval/store_provider.py
A backend/app/core/retrieval/document_reader.py
A backend/app/modules/docgen/...
A backend/tests/unit/retrieval/test_store_provider.py
A backend/tests/unit/retrieval/test_document_reader.py
A backend/tests/unit/docgen/...
A backend/tests/integration/test_docgen_router.py
M product/design/v0-mockup-v2.html
```

Do not include:

```text
backend/data_eval/
backend/data/docgen/sessions/*.json
```

If session JSON files were created during manual testing, remove only those B1 temporary files after confirming the path is exactly under `backend/data/docgen/sessions/`.

### Task 12: Commit Strategy

Only commit when the user asks for commit/push.

Recommended split:

1. Backend B1 service:

```powershell
git add backend/app/main.py backend/app/core/retrieval/store_provider.py backend/app/core/retrieval/document_reader.py backend/app/modules/chat/tools.py backend/app/modules/docgen backend/tests/unit/retrieval/test_store_provider.py backend/tests/unit/retrieval/test_document_reader.py backend/tests/unit/docgen backend/tests/integration/test_docgen_router.py
git commit -m "feat: 新增B1文档生成后端能力"
```

2. Frontend B1 page:

```powershell
git add product/design/v0-mockup-v2.html
git commit -m "feat: 接入B1文档生成页面"
```

3. Plan document, if committing the plan:

```powershell
git add docs/plans/2026-07-02-b1-doc-generation-implementation-plan.md
git commit -m "docs: 补充B1文档生成实施计划"
```

Before push:

```powershell
git status --short
git log --oneline -3
```

Expected:

- `git status --short` has no B1 source changes left.
- Unrelated user changes are not accidentally staged.

---

## 5. PRD Coverage Checklist

- [ ] 选蓝本：`GET /docgen/blueprints` + B1 frontend cards.
- [ ] 记录 `doc_id`：`POST /docgen/sessions` session JSON.
- [ ] Agent 通读蓝本的用户感知：summary from outline/summaries only.
- [ ] 不展示参数清单：blueprint prompt and UI summary do not list full params.
- [ ] 自然语言需求：`POST /docgen/sessions/{id}/plan`.
- [ ] 定位受影响节点：`search_nodes` then `read_node`.
- [ ] 计算能力：calculator shell plus AI source labeling.
- [ ] 改动清单：`ChangePlanItem`.
- [ ] 来源标签：`source_type`, `source_label`, `source_handles`.
- [ ] 勾选 / 取消勾选：`PATCH /plan`.
- [ ] 确认生成：`POST /draft`.
- [ ] 初稿待审核：draft response and frontend badge.
- [ ] 改动高亮：`highlighted_markdown` with `docgen-change-user` / `docgen-change-ai`.
- [ ] AI 推理值强调：AI CSS class with dashed underline.
- [ ] 复制全文：frontend copy button.
- [ ] 不入库：renderer/storage write only `data/docgen`.
- [ ] 不生成 `.docx`：no export endpoint.
- [ ] 不导出 MD/JSON：no export button or route.

---

## 6. Common Failure Modes And Required Handling

- **LLM returns invalid JSON:** return HTTP 502, do not overwrite previous plan.
- **Search index missing:** return HTTP 503 with clear detail; do not answer “没有找到”.
- **Unknown session:** HTTP 404.
- **Unknown plan item on patch:** HTTP 400.
- **`old_text` appears zero times:** leave draft unchanged for that item, add warning.
- **`old_text` appears multiple times:** leave draft unchanged for that item, add warning.
- **User deselects AI inferred item:** draft must skip it.
- **Frontend refresh loses in-memory state:** first implementation may require user to restart from blueprint list; `GET /docgen/sessions/{id}` exists for later restoration.
- **DOMPurify strips unexpected tags:** use `<mark>` only; avoid custom elements.
- **Manual test creates temporary session files:** clean only `backend/data/docgen/sessions/*.json`, never clean ingest/index directories.

---

## 7. Execution Notes For Future Agent

- Read files with UTF-8. On Windows PowerShell, raw `Get-Content` may display Chinese incorrectly in some consoles; use Python `Path.read_text(encoding="utf-8")` when judging Chinese text.
- Before editing, run `git status --short` and do not revert unrelated user changes.
- Current repository may already have unrelated dirty files. Stage only B1 files listed in this plan.
- Use `apply_patch` for manual edits.
- Do not use destructive git commands.
- Prefer tests with fake store/fake agent over real LLM.
- Real LLM tests are manual only because they need `.env` and proxy availability.
