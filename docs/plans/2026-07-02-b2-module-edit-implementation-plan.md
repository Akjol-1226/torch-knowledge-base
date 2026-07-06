# B2 Module Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read Chinese files with explicit UTF-8.

**Goal:** Build B2 "现有文档模块级编辑": in the existing knowledge-base document structure modal, let administrators edit a selected node's own body text manually or with AI assistance, immediately publish the change to the formal knowledge base, keep a document-level version/history timeline, and support rollback.

**Architecture:** Add a new `docedit` business module under `backend/app/modules/docedit/`. It owns edit/version/history APIs and LLM-assisted edit diff generation. It must update the formal PageIndex artifacts consistently: parsed Markdown under `data/md`, workspace tree JSON under `data/workspace`, catalog under `data/catalog`, and indexes under `data/indexes`. The existing tree modal in `product/design/v0-mockup-v2.html` remains the only UI surface; `product/design/b2-module-edit-mockup.html` is a visual/state reference only.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, LangChain `ChatOpenAI` through LiteLLM Proxy, existing `TreeStore` retrieval, existing `INDEX_LOCK`, existing `rebuild_from_workspace()`, static HTML/Tailwind frontend in `product/design/v0-mockup-v2.html`.

---

## 0. Scope, Defaults, And Non-Negotiables

### Source References

- PRD: `product/prd/b1-b2-doc-authoring.md`
- B2 design mockup: `product/design/b2-module-edit-mockup.html`
- Current served frontend: `product/design/v0-mockup-v2.html`
- Current document tree API: `backend/app/modules/ingest/document_service.py`
- Current structure modal node body API: `backend/app/modules/chat/router.py`
- Current retrieval store: `backend/app/core/retrieval/treestore.py`
- Current index rebuild entry: `backend/app/modules/ingest/tree_service.py`
- Current shared auth dependency placeholder: `backend/app/modules/auth/dependencies.py`

### B2 Requirements Covered

- B2 is not a new page. It extends the current "知识库 > 查看结构" document structure modal.
- Only administrators can edit formal documents.
- Normal users can still view structure and node body text, but cannot save, AI-edit, accept AI output, or rollback.
- Edit target is the selected node's **own body text**.
- Nodes with children can still be editable when they have their own body text.
- Pure container nodes with no own body text are not editable.
- AI edit reads only the selected node's own text and returns a red/delete + green/insert diff for review.
- Manual save or AI accept immediately updates the formal document.
- Version is document-level, not node-level.
- Node only displays a "改过" marker when changed.
- History is one document-level timeline, with diff details and rollback.
- Rollback appends a new version/change record; it must not mutate or delete old history.
- A saved edit must refresh retrieval data used by chat/B1.

### First Implementation Decisions

- **Module name:** use `docedit`, not `docgen`, `chat`, or `ingest`.
- **Model:** reuse `settings.chat_model` and the existing LiteLLM Proxy settings for AI edit. Do not add `DOCEDIT_MODEL` in the first pass.
- **AI edit granularity:** v0 should support AI edit for single physical nodes only. If a UI display node represents multiple merged windows, reject AI edit and ask the user to select a concrete segment.
- **Manual edit granularity:** manual edit also targets one physical node's own text only. Do not edit descendants.
- **Body-only editing:** v0 does not edit title, hierarchy, `node_id`, page mapping, or child structure.
- **Empty body:** v0 does not create body text for pure containers.
- **Acceptance mode:** AI diff is accepted/discarded as a whole block. Do not implement per-change accept/discard inside one node in v0.
- **History storage:** use SQLModel tables for `DocumentEditState`, `DocumentChange`, and `NodeEditState`.
- **Formal content storage:** the edited content must be written to both Markdown and workspace JSON.
- **Index refresh:** use `rebuild_from_workspace()` after content changes. This avoids full PDF reparse and full PageIndex rebuild while still publishing updated BM25/vector/catalog artifacts.
- **Summary refresh:** mark affected summaries dirty synchronously. Actual LLM summary regeneration can be async and may be delivered in a second step, but stale state must be visible and tracked.
- **Conflict control:** request must include `base_version` and `base_hash`; mismatch returns HTTP 409.
- **Concurrent reparse:** block B2 edits while the same document has active reparse task status `queued`, `processing`, or `needs_review`.
- **Snapshot/rollback safety:** writing formal content and rebuilding indexes must be atomic from the user's perspective. Reuse the snapshot/restore style from `commit_reparse_candidate()`.
- **Frontend integration:** integrate into `product/design/v0-mockup-v2.html`; keep `product/design/b2-module-edit-mockup.html` as reference only.

### Review Fixes That Must Stay In The Implementation

These constraints were added after implementation-readiness review. Do not weaken them during coding:

- **Save/rollback must be serialized and revalidated inside the locks.** Two saves with the same `base_version/base_hash` must not both pass. Use an async `DOCEDIT_SAVE_LOCK` to serialize docedit service calls, then run the synchronous artifact section under existing `INDEX_LOCK`. The `INDEX_LOCK` section must reload workspace node text, recheck document existence, recheck active reparse, recheck `base_hash`, prepare Markdown/workspace/pagemap replacement, snapshot artifacts, write files, and call `rebuild_from_workspace()`. There is no separate active-delete task state in v0; document deletion is detected by reload under `INDEX_LOCK`.
- **Snapshot lifetime covers file publish plus DB commit.** The service layer owns the snapshot. If file/index publish succeeds but DB flush/commit fails, restore Markdown, workspace JSON, catalog, pagemap, OCR metadata, domain dict, and indexes before returning an error.
- **`run_in_threadpool()` only wraps synchronous file/index work.** Never pass `AsyncSession` or async repository calls into the threadpool.
- **Markdown replacement must prove it is replacing the exact old block.** Check Markdown line count against workspace `line_count`, check start heading, check extracted old block equals workspace `node["text"]` after normalization, and reject if the old body contains another Markdown heading.
- **Pagemap must stay consistent.** If line count changes, shift `.pagemap.json` line breakpoints after the edited block by the same delta and include pagemap in snapshot/restore. Do not leave stale source line mapping.
- **External edit text is body-only.** API `text`, `before_text`, `after_text`, and `base_hash` are based on the node's own body text without the heading. Workspace `node["text"]` remains `heading + body`.
- **Merged sections still render through `/chat/node`.** `/docedit/nodes/{handle}` is for edit metadata and body for single physical nodes; merged `windows > 1` display still relies on `/chat/node` `context[]` for full read-only display.
- **Tests must not touch real `backend/data` or `backend/data_eval`.** Every docedit workspace/service/router test must force `DATA_DIR=tmp_path` and clear settings cache before use.
- **Vector failure policy follows existing retrieval design.** BM25/catalog rebuild success is enough for a successful publish. Vector index rebuild is best-effort inside current `rebuild_from_workspace()`; if embedding/vector fails, the system may temporarily run BM25-only or keep old vector artifacts until the next successful vector rebuild. Tests must assert this behavior explicitly instead of expecting full publish rollback on vector failure.

### Explicit Non-Goals For First Implementation

- Do not create a new B2 route/page in the frontend.
- Do not rerun PDF -> MD parsing for B2 edits.
- Do not rerun PageIndex full tree building for a small edit.
- Do not modify `backend/app/core/pageindex/*` parsing behavior for B2.
- Do not write B2 edit logic into `/chat/node`; keep it read-only.
- Do not add workflow approval or two-person review.
- Do not edit raw PDF files.
- Do not implement `.docx` export.
- Do not implement external reference documents.
- Do not implement mobile-specific UI.
- Do not stage/commit unrelated B1 or local workspace changes unless the user explicitly asks.

---

## 1. Current Code Facts

### Formal Document Read Path

- Markdown full text:
  - `backend/app/modules/ingest/router.py`
  - `GET /ingest/document/{doc_id}`
  - `document_service.get_document()`
  - Loads `data/workspace/<doc_id>.json`, reads `doc["path"]`, then reads parsed `.md`.

- Structure tree:
  - `GET /ingest/document/{doc_id}/tree`
  - `document_service.get_tree()`
  - Loads `workspace` JSON `structure`.
  - Returns title/page/summary/windows/has_text/children, but not body text.

- Structure modal body:
  - `GET /chat/node?text_only=1&handle=<doc_id>:<node_id>`
  - `chat.router.node_context()`
  - Uses `TreeStore.read_node()`.
  - Returns `context[]` with body text for the selected node or merged-section members.

- Retrieval:
  - `TreeStore` loads `data/workspace/doc_*.json`.
  - `search_nodes()` uses BM25/vector indexes under `data/indexes`.
  - `get_tree_store()` refreshes only when catalog/vector metadata mtimes change.

### Important Existing Behavior To Preserve

`document_service.get_tree()` already treats a node with children and own text as body-displayable:

```python
has_text = any(
    s and not re.match(r"#{1,6}\s", s)
    for s in (ln.strip() for ln in own.split("\n"))
)
```

`product/design/v0-mockup-v2.html` already mirrors this:

```js
const showsText = !hasKids || isSection || !!node.has_text;
```

B2 must not regress this. The editability rule is not "leaf only"; it is "has own body text and maps to a single editable physical node".

### Current Persistence Gap

The SQLModel `Document/Page/IngestTask` tables under `backend/app/modules/ingest/models.py` are not the source of truth for PageIndex document body text. The formal body text lives in:

- `data/md/<kb>/<stem>.md`
- `data/workspace/doc_*.json` under `structure[*].text`

B2 must update both or the system will split:

- B1 draft rendering reads Markdown.
- Chat/retrieval reads workspace/TreeStore.

### Current Summary Gap

Some PageIndex code may write `prefix_summary` for parent nodes, while `TreeStore`, `iter_nodes()`, and `document_service.get_tree()` read `summary`. Before relying on regenerated ancestor summaries, implementation must normalize the read/write field or explicitly copy regenerated values into `summary`.

---

## 2. File Map

### Create

- `backend/app/modules/docedit/__init__.py`
  - Module marker.
- `backend/app/modules/docedit/models.py`
  - SQLModel tables for document edit state and history.
- `backend/app/modules/docedit/schemas.py`
  - Pydantic request/response models.
- `backend/app/modules/docedit/repository.py`
  - Async DB access for state/history.
- `backend/app/modules/docedit/workspace.py`
  - Formal document read/write helpers: parse handle, locate node, replace body text in Markdown, update workspace, snapshot/restore.
- `backend/app/modules/docedit/diff.py`
  - Text hash and safe HTML diff helpers.
- `backend/app/modules/docedit/agent.py`
  - LiteLLM-backed AI edit call and JSON validation.
- `backend/app/modules/docedit/prompts/ai_edit.md`
  - Prompt for scoped node-only AI editing.
- `backend/app/modules/docedit/service.py`
  - Orchestrates permissions, conflict checks, file/index updates, history, rollback.
- `backend/app/modules/docedit/router.py`
  - `/docedit/*` routes.
- `backend/tests/unit/docedit/test_workspace.py`
  - Node locating and Markdown/workspace replacement tests.
- `backend/tests/unit/docedit/test_diff.py`
  - Hash and diff tests.
- `backend/tests/unit/docedit/test_agent.py`
  - JSON extraction/validation tests.
- `backend/tests/unit/docedit/test_service.py`
  - Service orchestration tests with fake workspace/agent/repository.
- `backend/tests/integration/test_docedit_router.py`
  - API permission, conflict, save, AI diff, history, rollback tests.

### Modify

- `backend/app/main.py`
  - Import and register `docedit_router`.
- `backend/app/modules/ingest/document_service.py`
  - Add optional edit metadata to tree response or keep tree unchanged and have frontend call `/docedit/documents/{doc_id}/state`.
  - Preferred: add only minimal fields if needed; keep ingest as document-view service, not edit service.
- `product/design/v0-mockup-v2.html`
  - Add B2 CSS classes for inline toolbar, textarea, AI panel, diff, touched badge, history view, version badge.
  - Add "修改历史" button to `#tree-modal` header.
  - Extend `makeTreeNode()` detail rendering to call `/docedit/nodes/{handle}` for editable state/body.
  - Add manual edit, AI diff, accept, discard, rollback, and history JS functions.

### Do Not Touch

- `backend/app/modules/chat/router.py` except if a tiny response field is absolutely required. Keep `/chat/node` read-only.
- `backend/app/core/retrieval/treestore.py` for write logic. It should remain a read/search store.
- `backend/app/core/pageindex/*` parsing/building internals.
- `backend/app/modules/ingest/docparse_service.py`
- `backend/app/modules/ingest/task_worker.py`
- `backend/app/modules/ingest/reparse_worker.py`
- `backend/app/modules/ingest/doc_convert.py`
- `backend/data_eval/`
- `backend/data/md/`
- `backend/data/workspace/`
- `backend/data/catalog/`
- `backend/data/indexes/`
- `product/design/b2-module-edit-mockup.html` unless the user explicitly asks to update the standalone prototype.

---

## 3. Data Model

Use SQLModel and let `init_db()` create tables.

### `DocumentEditState`

Purpose: one row per PageIndex document, tracks document-level edit version and summary state.

Fields:

- `doc_id: str` primary key. This is PageIndex `doc_id`, not `ingest.models.Document.id`.
- `current_version: int`, default `1`.
- `summary_status: str`, one of `clean`, `stale`, `regenerating`, `failed`.
- `active_summary_task_id: str | None`.
- `created_at: datetime`.
- `updated_at: datetime`.

Behavior:

- If no row exists for an existing document, initialize lazily as `current_version=1`.
- Version increments for every manual edit, AI accepted edit, and rollback.

### `DocumentChange`

Purpose: append-only document-level history timeline.

Fields:

- `id: int | None` primary key.
- `doc_id: str`, indexed.
- `version_from: int`.
- `version_to: int`, indexed.
- `node_handle: str`, indexed.
- `node_title: str`.
- `node_path: str`.
- `op_type: str`, one of `manual_edit`, `ai_edit`, `rollback`.
- `actor_user_id: int | None`.
- `actor_username: str`.
- `before_text: str`.
- `after_text: str`.
- `before_hash: str`.
- `after_hash: str`.
- `diff_html: str`.
- `ai_instruction: str | None`.
- `ai_explanation: str | None`.
- `source_change_id: int | None`, only for rollback.
- `created_at: datetime`, indexed.

Rules:

- Append-only. Never update old history rows to "undo" something.
- Rollback creates a new row whose `op_type` is `rollback`.
- History list uses `version_to` descending.

### `NodeEditState`

Purpose: fast "改过" marker and per-node last-change metadata.

Fields:

- `id: int | None` primary key.
- `doc_id: str`, indexed.
- `node_handle: str`, unique/indexed.
- `original_hash: str`.
- `current_hash: str`.
- `last_change_id: int`.
- `changed: bool`.
- `summary_status: str`, one of `clean`, `stale`, `regenerating`, `failed`.
- `updated_at: datetime`.

Rules:

- First edit stores `original_hash` from the pre-edit body.
- `changed` may become false only if current hash equals original hash, but keep history.

---

## 4. API Contract

### `GET /docedit/documents/{doc_id}/state`

Returns edit metadata for the current document.

Response:

```json
{
  "doc_id": "doc_x",
  "version": 4,
  "can_edit": true,
  "summary_status": "stale",
  "changed_node_ids": ["doc_x:0001", "doc_x:0007"],
  "active_reparse": false
}
```

Notes:

- `can_edit` comes from current user permission and document state.
- `active_reparse=true` means UI should disable editing and explain that reparse is running.

### `GET /docedit/nodes/{handle}`

Returns editable node body and editability metadata.

Response:

```json
{
  "handle": "doc_x:0001",
  "doc_id": "doc_x",
  "title": "5.1 工序参数",
  "path": "文档 > 5 涂布工序 > 5.1 工序参数",
  "text": "涂布厚度控制在 12±2 μm，涂布速度 8.0 m/min。",
  "text_hash": "sha256:0123456789abcdef",
  "doc_version": 4,
  "has_children": false,
  "has_text": true,
  "editable": true,
  "editable_reason": null,
  "can_edit": true,
  "changed": false,
  "summary_status": "clean"
}
```

`editable_reason` values:

- `not_admin`
- `container_only`
- `empty_text`
- `merged_section`
- `active_reparse`
- `unknown_node`

### `PATCH /docedit/nodes/{handle}`

Manual save or AI accept. Persists formal content.

Request:

```json
{
  "base_version": 4,
  "base_hash": "sha256:0123456789abcdef",
  "after_text": "涂布厚度控制在 12±2 μm，涂布速度 8.0 m/min。",
  "op_type": "manual_edit",
  "ai_instruction": null,
  "ai_explanation": null
}
```

Response:

```json
{
  "handle": "doc_x:0001",
  "doc_id": "doc_x",
  "version": 5,
  "text": "涂布厚度控制在 12±2 μm，涂布速度 8.0 m/min。",
  "text_hash": "sha256:abcdef0123456789",
  "changed": true,
  "change_id": 12,
  "summary_status": "stale",
  "index_rebuilt": true
}
```

Errors:

- `403`: user is not admin.
- `404`: document or node not found.
- `409`: `base_version` or `base_hash` mismatch, or active reparse.
- `422`: empty/invalid `after_text`.
- `503`: index rebuild failed after restore.

### `POST /docedit/nodes/{handle}/ai-diff`

Generates AI edit preview. Does not persist.

Request:

```json
{
  "base_version": 4,
  "base_hash": "sha256:0123456789abcdef",
  "instruction": "把烧结温度改成 200 ℃，其它不动"
}
```

Response:

```json
{
  "handle": "doc_x:0001",
  "before_text": "涂布厚度控制在 12±2 μm，烧结温度 180 ℃。",
  "after_text": "涂布厚度控制在 12±2 μm，烧结温度 200 ℃。",
  "before_hash": "sha256:0123456789abcdef",
  "after_hash": "sha256:abcdef0123456789",
  "diff_html": "涂布厚度控制在 12±2 μm，烧结温度 <del>180 ℃</del><ins>200 ℃</ins>。",
  "explanation": "仅修改烧结温度，其它参数保持不变。"
}
```

Prompt constraints:

- AI must modify only the provided node text.
- AI must not reference or modify descendants.
- AI must return JSON only.
- If the instruction is ambiguous, return an explanation and unchanged text.

### `GET /docedit/documents/{doc_id}/history`

Returns document-level timeline.

Response:

```json
{
  "doc_id": "doc_x",
  "version": 5,
  "items": [
    {
      "change_id": 12,
      "version": 5,
      "op_type": "manual_edit",
      "node_handle": "doc_x:0001",
      "node_title": "5.1 工序参数",
      "node_path": "文档 > 5 涂布工序 > 5.1 工序参数",
      "actor_username": "admin",
      "created_at": "2026-07-02T10:30:00Z",
      "can_rollback": true
    }
  ]
}
```

### `GET /docedit/changes/{change_id}`

Returns a single diff.

Response:

```json
{
  "change_id": 12,
  "doc_id": "doc_x",
  "version_from": 4,
  "version_to": 5,
  "node_handle": "doc_x:0001",
  "before_text": "烧结温度 180 ℃。",
  "after_text": "烧结温度 200 ℃。",
  "diff_html": "烧结温度 <del>180 ℃</del><ins>200 ℃</ins>。",
  "ai_instruction": null,
  "ai_explanation": null
}
```

### `POST /docedit/changes/{change_id}/rollback`

Rollback one change by applying its `before_text` as the current text. Rollback itself creates a new version.

Request:

```json
{
  "base_version": 5,
  "base_hash": "sha256:current-node-text"
}
```

Response:

```json
{
  "doc_id": "doc_x",
  "rolled_back_change_id": 12,
  "change_id": 13,
  "version": 6,
  "node_handle": "doc_x:0001",
  "text_hash": "sha256:abcdef0123456789"
}
```

Conflict rule:

- Default rollback only succeeds when current node hash equals the target change's `after_hash`.
- If current node changed since that history entry, return `409` instead of silently overwriting later edits.

---

## 5. Backend Design

### `workspace.py`

Responsibilities:

- `parse_handle(handle: str) -> tuple[str, str]`
  - Split `<doc_id>:<node_id>`.
  - Reject malformed handles.
- `load_workspace_doc(doc_id: str) -> WorkspaceDocument`
  - Read `settings.data_dir / "workspace" / f"{safe_name(doc_id)}.json"`.
  - Validate `doc["path"]` points under `settings.data_dir / "md"`.
- `find_node(doc: dict, node_id: str) -> LocatedNode`
  - Traverse nested `structure`.
  - Return node dict, ancestors, preorder list, previous/next line context.
- `extract_body_text(text: str) -> str`
  - Remove the first Markdown heading line and return body-only text.
- `has_own_body_text(text: str) -> bool`
  - Reuse the same body-detection rule as `document_service.get_tree()`.
- `is_single_physical_node(handle) -> bool`
  - Use workspace structure and/or `TreeStore.read_node(handle).section`.
  - Reject `section.members` length > 1 in v0.
- `replace_node_body_in_markdown(markdown: str, node, next_node) -> tuple[str, int]`
  - Replace only body lines for this node.
  - Keep original title line and heading marker.
  - Return new markdown and line-count delta.
- `update_workspace_node_text(doc, node, after_text, line_delta) -> dict`
  - Update node `text`.
  - Update `line_count`.
  - Adjust `line_num` for following nodes after the edited block.
  - Mark node and ancestors summary status fields if stored in JSON.
- `snapshot_live_artifacts(doc) -> ArtifactSnapshot`
  - Snapshot Markdown, workspace JSON, pagemap, OCR sidecars, catalog, workspace `_meta`, domain dict, indexes dir.
- `restore_live_artifacts(snapshot) -> None`
  - Restore on failure.

Important implementation detail:

- If replacing body in Markdown is ambiguous, fail with a clear error and restore. Do not silently update only workspace.
- For v0, preserve heading line exactly; users edit body text only.

### `diff.py`

Functions:

- `text_hash(text: str) -> str`
  - Use SHA-256, prefix with `sha256:`.
- `make_diff_html(before: str, after: str) -> str`
  - Use `difflib.SequenceMatcher` or line/word-level diff.
  - Escape HTML before wrapping in `<del>` / `<ins>`.
  - Do not pass raw AI HTML to frontend.

### `agent.py`

Follow B1's `docgen.agent` pattern:

- Read prompt from `prompts/ai_edit.md`.
- Build `ChatOpenAI` with:
  - `model=settings.chat_model`
  - call `settings.apply_litellm_env()` before construction.
  - `api_key=settings.litellm_api_key.get_secret_value()`
  - `base_url=settings.litellm_base_url`
  - `temperature=0`
  - timeout and retry values matching `backend/app/modules/docgen/agent.py`.
- Parse JSON robustly with `extract_json_object()`.
- Validate response with Pydantic:
  - `after_text: str`
  - `explanation: str`
  - `changed: bool`

Prompt must include:

- Document/node metadata only for context.
- Current node text.
- User instruction.
- Explicit boundary: "只改当前节点正文，不要新增/删除章节，不要改子节点，不要输出 Markdown fence".

### `service.py`

Public service functions:

- `get_document_state(doc_id, user) -> DocumentStateResponse`
- `get_node(handle, user) -> EditableNodeResponse`
- `create_ai_diff(handle, req, user) -> AiDiffResponse`
- `save_node(handle, req, user) -> SaveNodeResponse`
- `list_history(doc_id, user) -> HistoryResponse`
- `get_change(change_id, user) -> ChangeDetailResponse`
- `rollback_change(change_id, req, user) -> RollbackResponse`

Save flow:

1. Require `user.is_admin`; otherwise raise `DoceditForbidden`.
2. Check active reparse with `task_service.find_active_reparse(doc_id)`.
3. Acquire the docedit save critical section. This must serialize manual save, AI accept, and rollback for the same formal artifact set.
4. Inside the lock, reload current edit state and current node from the latest DB/workspace state.
5. Recheck active reparse and document existence inside the lock.
6. Validate `base_version == current_version`.
7. Validate `base_hash == text_hash(current_node.body_text)`.
8. Validate node editable:
   - has own body text.
   - not a pure container.
   - not a merged-section display node in v0.
9. Build `diff_html`.
10. Snapshot live artifacts at service level.
11. Insert `DocumentChange`; `flush()` DB only, do not commit.
12. Bump `DocumentEditState.current_version` with `bump_document_version()`.
13. Upsert `NodeEditState` and mark affected summaries stale.
14. Run only the synchronous Markdown/workspace/pagemap/index publish function in `run_in_threadpool()`.
15. Commit DB.
16. On any exception after snapshot, rollback DB, restore artifacts, and re-raise mapped error.

Rollback flow:

1. Load target change.
2. Require admin.
3. Load current node.
4. Validate `base_version` and `base_hash`.
5. Validate current hash equals target `after_hash`; else return conflict.
6. Reuse save flow with `after_text=target.before_text`, `op_type=rollback`, and `source_change_id=target.id`.

AI diff flow:

1. Require admin.
2. Check active reparse.
3. Validate current node and `base_version/base_hash`.
4. Call AI agent with current node body only.
5. Normalize line endings.
6. Compute diff server-side from current text to AI `after_text`.
7. Return preview only; do not write DB/file/index.

### Error Mapping

Router maps service exceptions:

- `DoceditForbidden` -> 403
- `DoceditNotFound` -> 404
- `DoceditConflict` -> 409
- `DoceditBadRequest` -> 400
- `DoceditUnprocessableEntity` -> 422
- `DoceditUpstreamError` -> 502
- `DoceditServiceUnavailable` -> 503

Threadpool rule:

- Service/router functions remain async and keep using `AsyncSession` on the event loop.
- Only synchronous file, workspace, pagemap, artifact snapshot/restore, and `rebuild_from_workspace()` work is wrapped in `run_in_threadpool()`.
- Do not pass `AsyncSession`, async repository methods, or request objects into the threadpool.

---

## 6. Frontend Design

### Integration Points

Existing code:

- `#tree-modal`
- `#tree-view`
- `#tree-reparse`
- `#tree-expand-all`
- `#tree-collapse-all`
- `currentTreeDocId`
- `currentTreeDocName`
- `currentTreeHasPdf`
- `treeOpenSeq`
- `openDocTree(docId)`
- `makeTreeNode(node, depth, num)`

Add:

- `#tree-history` button in modal header.
- Document version badge near `#tree-modal-title`.
- `treeEditState` object:

```js
let treeViewMode = 'structure';
let currentTreeVersion = null;
let currentTreeCanEdit = false;
const treeNodeUi = new Map();
const treeTouchedNodes = new Set();
```

Node UI state:

```js
{
  mode: 'view' | 'edit' | 'ai' | 'ai-loading' | 'diff' | 'saving' | 'error',
  text: '',
  textHash: '',
  baseVersion: 1,
  aiInstruction: '',
  pending: null,
  opSeq: 0,
  error: ''
}
```

### Tree Loading

`openDocTree(docId)` should:

1. Fetch `/ingest/document/{doc_id}/tree` as today.
2. Fetch `/docedit/documents/{doc_id}/state`.
3. Store `currentTreeVersion`, `currentTreeCanEdit`, `treeTouchedNodes`.
4. Render tree with:
   - existing title/page/windows.
   - "改过" badge for changed handles.
   - disabled editing state if `active_reparse`.

If `/docedit/documents/{doc_id}/state` fails because backend route is not available, do not break read-only structure viewing. Hide edit controls and show no version badge.

### Node Detail Loading

When a body-displayable node is clicked:

- Use existing `showsText = !hasKids || isSection || !!node.has_text`.
- Keep `/chat/node?text_only=1&handle=<encoded_handle>` as the read/display source, because merged display sections currently return `context[]` with multiple member texts.
- Fetch `/docedit/nodes/{encodeURIComponent(node.id)}` as the edit metadata source for single physical nodes: `editable`, `editable_reason`, `can_edit`, `text_hash`, `doc_version`, and changed state.
- For non-merged single nodes, `/docedit/nodes` may also provide body-only edit text. The visible read body must remain equivalent to the existing `/chat/node` rendering.
- For `windows > 1` or `editable_reason="merged_section"`, render the full read-only body from `/chat/node` and hide save/AI controls.
- Fallback to `/chat/node` only when `/docedit` is truly unavailable: network failure, 404, or 405. Do not silently fallback on 403, 409, 422, 500, 502, or 503; show the returned reason.

Render:

- `view`:
  - toolbar: "当前正文".
  - admin + editable: "编辑", "AI编辑".
  - admin + not editable: disabled reason text.
  - non-admin: no edit buttons.
  - Markdown body via `mdToHtml()` and `renderMathIn()`.
- `edit`:
  - textarea with full body text.
  - "保存" / "取消".
  - save text: "保存即记入文档修改历史".
- `ai`:
  - read-only faded current body.
  - instruction textarea.
  - "发送" / "取消".
- `ai-loading`:
  - disable controls.
  - status: "AI 正在只改本块".
- `diff`:
  - "AI 已读本块原文，按「用户指令」只改了这一块".
  - diff HTML from backend, sanitized before insert.
  - "接受改动" / "重新说" / "放弃".
- `saving`:
  - disable controls to prevent duplicate save.
- `error`:
  - show error and recovery buttons.

### Non-Leaf Nodes With Own Text

Required behavior:

- If node has children and `has_text=true`, it must still show its own body detail.
- Manual edit can be offered if backend `editable=true`.
- AI edit can be offered only if backend `editable=true` and not `merged_section`.
- The edit applies only to the selected node's own body text, never children.
- Pure containers (`hasKids && !showsText`) only expand/collapse and show a short hint if needed.

### History View

`#tree-history` toggles `#tree-view` between structure and document history.

History view:

- Header text: "整份文档一条时间线".
- Rows show:
  - `vN`
  - changed node title/path
  - date
  - actor
  - op type
  - "查看 diff"
  - "回退此改动" only for admin and eligible rows.
- `GET /docedit/changes/{change_id}` lazily loads diff.
- Rollback first reloads the current node via `/docedit/nodes/{nodeHandle}` to obtain latest `base_version/base_hash`, then calls `POST /docedit/changes/{change_id}/rollback`, then refreshes history and current tree edit state.
- When returning from history to structure, rebuild the structure DOM from `currentTreeRoots`. Either restore `treeExpandedNodes` / `treeOpenDetailNodes`, or explicitly reset the tree to its default collapsed state. Do not rely on old DOM after replacing `#tree-view.innerHTML`.

### Frontend Safety

- Keep `#tree-view` as the only main scroll region; avoid page-level overflow issues.
- Do not reuse B1 names like `docgenState.copyText`.
- Do not affect `#doc-modal` Markdown/PDF viewing.
- Check `treeOpenSeq`, current `docId`, and current node id before applying async results.
- Set `detail.dataset.handle = node.id` immediately when creating node detail DOM; all stale-response guards depend on it.
- Track a per-node `opSeq` or `pendingRequestId`. Increment it before AI diff, save, accept, rollback, or detail refresh, and verify it after every await so older responses for the same node cannot overwrite newer UI state.
- Sanitize any HTML diff before assigning `innerHTML`; allow only `<del>` and `<ins>` after DOMPurify.
- Continue using `mdToHtml()` for normal body Markdown.

---

## 7. Implementation Tasks

### Task 1: Add Docedit Models And Repository

**Files:**

- Create `backend/app/modules/docedit/__init__.py`
- Create `backend/app/modules/docedit/models.py`
- Create `backend/app/modules/docedit/repository.py`
- Test `backend/tests/unit/docedit/test_repository.py`

- [ ] Define SQLModel tables `DocumentEditState`, `DocumentChange`, `NodeEditState`.
- [ ] Use `str` PageIndex `doc_id`, not old integer `Document.id`.
- [ ] Implement lazy `get_or_create_document_state(doc_id)`.
- [ ] Implement `bump_document_version()`.
- [ ] Implement append-only `create_change()`.
- [ ] Implement history list by `doc_id`, newest first.
- [ ] Implement node edit state upsert.
- [ ] Add repository tests using temporary SQLite async session.
- [ ] Update `backend/tests/conftest.py` to import `docedit` models before `SQLModel.metadata.create_all`:
  `from app.modules.docedit import models as _docedit_models  # noqa: F401`.
- [ ] Ensure runtime router registration imports `docedit.models` before `init_db()` creates metadata tables.

Verification:

```powershell
cd backend
uv run pytest tests/unit/docedit/test_repository.py -v
```

### Task 2: Add Workspace Read/Write Helpers

**Files:**

- Create `backend/app/modules/docedit/workspace.py`
- Test `backend/tests/unit/docedit/test_workspace.py`

- [ ] Implement handle parsing.
- [ ] Implement workspace document loading with safe path checks.
- [ ] Implement recursive node lookup with ancestors.
- [ ] Implement `has_own_body_text()` using the same rule as `document_service.get_tree()`.
- [ ] Implement editable classification:
  - single physical node
  - has own text
  - not pure container
  - not active merged section
- [ ] Implement body-only Markdown replacement.
- [ ] Preserve heading line.
- [ ] Reject replacement when Markdown line count differs from workspace `line_count`.
- [ ] Reject replacement when the Markdown block selected by `line_num` does not equal workspace `node["text"]` after normalization.
- [ ] Reject replacement when the old block body or new `after_text` contains Markdown heading lines.
- [ ] Update workspace node `text`.
- [ ] Adjust `line_count` and later node `line_num` when line count changes.
- [ ] Shift `.pagemap.json` Markdown line breakpoints after the edited block by `line_delta`.
- [ ] Include pagemap in snapshot/restore.
- [ ] Add tests for:
  - leaf body replace.
  - node with children and own text replace.
  - pure container rejected.
  - line number shift.
  - pagemap line shift.
  - malformed handle.
  - Markdown/workspace mismatch rejected.

Verification:

```powershell
cd backend
uv run pytest tests/unit/docedit/test_workspace.py -v
```

### Task 3: Add Diff And AI Agent

**Files:**

- Create `backend/app/modules/docedit/diff.py`
- Create `backend/app/modules/docedit/agent.py`
- Create `backend/app/modules/docedit/prompts/ai_edit.md`
- Test `backend/tests/unit/docedit/test_diff.py`
- Test `backend/tests/unit/docedit/test_agent.py`

- [ ] Implement `text_hash()`.
- [ ] Implement escaped `<del>/<ins>` diff HTML.
- [ ] Implement JSON extraction helper, mirroring B1 `docgen.agent` style.
- [ ] Build `ChatOpenAI` from `settings.chat_model`, using the same `settings.apply_litellm_env()` and `settings.litellm_api_key.get_secret_value()` pattern as B1 `docgen.agent`.
- [ ] Validate AI response with Pydantic.
- [ ] Prompt: only current node text may be changed.
- [ ] Unit test AI parsing without real LLM.

Verification:

```powershell
cd backend
uv run pytest tests/unit/docedit/test_diff.py tests/unit/docedit/test_agent.py -v
```

### Task 4: Implement Docedit Service Save Flow

**Files:**

- Create `backend/app/modules/docedit/service.py`
- Test `backend/tests/unit/docedit/test_service.py`

- [ ] Implement service exceptions.
- [ ] Implement `get_document_state()`.
- [ ] Implement `get_node()`.
- [ ] Implement admin checks.
- [ ] Implement active reparse check via `task_service.find_active_reparse(doc_id)`.
- [ ] Implement `save_node()` with:
  - service-level save lock/critical section.
  - reloading DB state and workspace node inside the lock.
  - active reparse/delete existence recheck inside the lock.
  - version conflict check.
  - hash conflict check.
  - editability check.
  - artifact snapshot covering publish plus DB commit.
  - DB history `flush()` before publish, then final `commit()` after publish.
  - document version bump via `bump_document_version()`.
  - node state update.
  - summary stale marker.
  - Markdown write.
  - workspace JSON write.
  - pagemap shift when line count changes.
  - `rebuild_from_workspace()`.
  - DB rollback and artifact restore on publish or DB failure.
- [ ] Add tests for:
  - non-admin 403 path.
  - base version conflict.
  - base hash conflict.
  - pure container rejected.
  - node with children and own text accepted.
  - history created.
  - index rebuild called.
  - restore called on failure.
  - restore called when DB commit fails after publish.
  - no history/version bump when publish fails.
  - active reparse statuses `queued`, `processing`, and `needs_review` are rejected; `done` and `failed` are not.

Verification:

```powershell
cd backend
uv run pytest tests/unit/docedit/test_service.py -v
```

### Task 5: Implement AI Diff And Rollback Service

**Files:**

- Modify `backend/app/modules/docedit/service.py`
- Test `backend/tests/unit/docedit/test_service.py`

- [ ] Implement `create_ai_diff()`.
- [ ] Ensure it does not write DB/files/indexes.
- [ ] Implement `list_history()`.
- [ ] Implement `get_change()`.
- [ ] Implement `rollback_change()`.
- [ ] Rollback must append new change row.
- [ ] Rollback must run the same formal content update path as save.
- [ ] Rollback must reject if current hash no longer equals target `after_hash`.

Verification:

```powershell
cd backend
uv run pytest tests/unit/docedit/test_service.py -v
```

### Task 6: Add Router And Register In Main

**Files:**

- Create `backend/app/modules/docedit/router.py`
- Modify `backend/app/main.py`
- Test `backend/tests/integration/test_docedit_router.py`

- [ ] Add `APIRouter(prefix="/docedit", tags=["docedit"])`.
- [ ] Use `Depends(get_current_user)`.
- [ ] Use `Depends(get_db)` for DB-backed operations.
- [ ] Keep router/service async. Wrap only synchronous file/index publish helpers in `run_in_threadpool()`; never pass `AsyncSession` into the threadpool.
- [ ] Map service exceptions to HTTP status codes.
- [ ] Register router in `main.py`.
- [ ] Explicitly import `app.modules.docedit.models` in `main.py` or the router module before `init_db()` runs; do not rely only on incidental service imports for SQLModel metadata registration.
- [ ] Integration tests:
  - get state.
  - get node.
  - manual save.
  - AI diff.
  - history.
  - change detail.
  - rollback.
  - permission denied.
  - conflict.

Verification:

```powershell
cd backend
uv run pytest tests/integration/test_docedit_router.py -v
```

### Task 7: Add Frontend Structure Modal UI

**Files:**

- Modify `product/design/v0-mockup-v2.html`

- [ ] Add CSS classes inspired by `b2-module-edit-mockup.html`:
  - `.docedit-version-badge`
  - `.tree-touched`
  - `.leaf-toolbar`
  - `.docedit-editor`
  - `.docedit-ai-bar`
  - `.docedit-diff`
  - `.docedit-history`
- [ ] Add `#tree-history` button in modal header.
- [ ] Add state:
  - `treeViewMode`
  - `currentTreeVersion`
  - `currentTreeCanEdit`
  - `treeNodeUi`
  - `treeTouchedNodes`
- [ ] Fetch `/docedit/documents/{doc_id}/state` in `openDocTree()`.
- [ ] Keep read-only fallback only when `/docedit` endpoints are unavailable by network failure/404/405.
- [ ] Do not fallback on `/docedit` 403/409/422/500/502/503; display the backend reason.
- [ ] Preserve `/chat/node` display path for merged `windows > 1` sections.
- [ ] Add changed badge in `makeTreeNode()`.
- [ ] Replace detail rendering with stateful `renderTreeNodeDetail(node, detail)`.
- [ ] Implement manual edit actions.
- [ ] Implement AI edit actions.
- [ ] Implement diff accept/retry/discard actions.
- [ ] Implement history toggle, diff lazy loading, rollback action.
- [ ] Preserve `treeOpenSeq` checks for async requests.
- [ ] Preserve existing reparse button behavior.
- [ ] Preserve existing expand/collapse behavior.
- [ ] Ensure non-leaf nodes with own text still show body and edit controls based on backend response.

Verification:

```powershell
node -e "const fs=require('fs');const s=fs.readFileSync('product/design/v0-mockup-v2.html','utf8');let n=0;for(const m of s.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)){new Function(m[1]);n++}console.log('checked',n,'scripts')"
```

Manual checks:

- Open `http://localhost:8000/`.
- Go to knowledge-base list.
- Click "结构".
- Expand node with body text.
- Confirm toolbar appears only when backend says editable.
- Confirm node with children and own text displays its body.
- Confirm pure container only expands.
- Confirm history toggles within same modal.

### Task 8: Summary Dirty State And Async Regeneration

**Files:**

- Modify `backend/app/modules/docedit/service.py`
- Possibly create `backend/app/modules/docedit/summary_worker.py`
- Tests under `backend/tests/unit/docedit/`

Minimum v0:

- [ ] Mark changed node and ancestors `summary_status=stale` in DB.
- [ ] Return `summary_status=stale` after save/rollback.
- [ ] Frontend shows a small "摘要更新中" or "摘要待更新" indicator.

Optional same-pass enhancement:

- [ ] Add background task to regenerate changed leaf summary.
- [ ] Add background task to regenerate ancestor subtree summaries.
- [ ] Write regenerated summaries to workspace `summary`.
- [ ] Call `rebuild_from_workspace()` after summary write.
- [ ] Mark summary status clean/failed.

Do not block first save response on long LLM summary regeneration.

---

## 8. Execution-Grade Implementation Manual

This section is the handoff material for the future implementation agent. Prefer the names, schemas, function signatures, and tests below unless the codebase has changed.

### 8.1 Backend File Skeleton

Create exactly this module layout:

```text
backend/app/modules/docedit/
  __init__.py
  agent.py
  diff.py
  models.py
  repository.py
  router.py
  schemas.py
  service.py
  workspace.py
  prompts/
    ai_edit.md
```

`docedit` owns all edit/version/history behavior. Do not put write behavior in `chat`, `ingest`, `TreeStore`, or `pageindex`.

### 8.2 `models.py`

Use SQLModel tables. `doc_id` is the PageIndex string id, not `ingest.models.Document.id`.

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import Column, String, Text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class DocumentEditState(SQLModel, table=True):
    __tablename__: ClassVar[str] = "document_edit_state"

    doc_id: str = Field(primary_key=True)
    current_version: int = Field(default=1, index=True)
    summary_status: str = Field(default="clean", index=True)
    active_summary_task_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DocumentChange(SQLModel, table=True):
    __tablename__: ClassVar[str] = "document_change"

    id: int | None = Field(default=None, primary_key=True)
    doc_id: str = Field(index=True)
    version_from: int
    version_to: int = Field(index=True)
    node_handle: str = Field(index=True)
    node_title: str
    node_path: str
    op_type: str = Field(index=True)
    actor_user_id: int | None = None
    actor_username: str = ""
    before_text: str = Field(sa_column=Column(Text, nullable=False))
    after_text: str = Field(sa_column=Column(Text, nullable=False))
    before_hash: str = Field(index=True)
    after_hash: str = Field(index=True)
    diff_html: str = Field(sa_column=Column(Text, nullable=False))
    ai_instruction: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    ai_explanation: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    source_change_id: int | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class NodeEditState(SQLModel, table=True):
    __tablename__: ClassVar[str] = "node_edit_state"

    id: int | None = Field(default=None, primary_key=True)
    doc_id: str = Field(index=True)
    node_handle: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    original_hash: str
    current_hash: str
    last_change_id: int = Field(index=True)
    changed: bool = Field(default=True, index=True)
    summary_status: str = Field(default="clean", index=True)
    updated_at: datetime = Field(default_factory=utc_now)
```

Rules:

- `DocumentEditState.current_version` starts at `1`.
- Every manual save, AI accept, and rollback increments the document version.
- `DocumentChange` is append-only.
- `NodeEditState.original_hash` is fixed at first edit; later edits update only `current_hash`.
- `NodeEditState.changed = current_hash != original_hash`.
- Valid `summary_status`: `clean`, `stale`, `regenerating`, `failed`.

### 8.3 `schemas.py`

Use stable field names because the static frontend will call these directly.

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SummaryStatus(StrEnum):
    clean = "clean"
    stale = "stale"
    regenerating = "regenerating"
    failed = "failed"


class EditableReason(StrEnum):
    not_admin = "not_admin"
    container_only = "container_only"
    empty_text = "empty_text"
    merged_section = "merged_section"
    active_reparse = "active_reparse"
    unknown_node = "unknown_node"


class SaveOpType(StrEnum):
    manual_edit = "manual_edit"
    ai_edit = "ai_edit"


class ChangeOpType(StrEnum):
    manual_edit = "manual_edit"
    ai_edit = "ai_edit"
    rollback = "rollback"


class DocumentStateResponse(BaseModel):
    doc_id: str
    version: int
    can_edit: bool
    summary_status: SummaryStatus
    changed_node_ids: list[str] = Field(default_factory=list)
    active_reparse: bool = False


class EditableNodeResponse(BaseModel):
    handle: str
    doc_id: str
    title: str
    path: str
    text: str
    text_hash: str
    doc_version: int
    has_children: bool
    has_text: bool
    editable: bool
    editable_reason: EditableReason | None = None
    can_edit: bool
    changed: bool
    summary_status: SummaryStatus


class SaveNodeRequest(BaseModel):
    base_version: int = Field(ge=1)
    base_hash: NonEmptyStr
    after_text: NonEmptyStr
    op_type: SaveOpType = SaveOpType.manual_edit
    ai_instruction: str | None = None
    ai_explanation: str | None = None

    @field_validator("after_text")
    @classmethod
    def normalize_after_text(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class SaveNodeResponse(BaseModel):
    handle: str
    doc_id: str
    version: int
    text: str
    text_hash: str
    changed: bool
    change_id: int
    summary_status: SummaryStatus
    index_rebuilt: bool


class AiDiffRequest(BaseModel):
    base_version: int = Field(ge=1)
    base_hash: NonEmptyStr
    instruction: NonEmptyStr


class AiDiffResponse(BaseModel):
    handle: str
    before_text: str
    after_text: str
    before_hash: str
    after_hash: str
    diff_html: str
    explanation: str


class HistoryItem(BaseModel):
    change_id: int
    version: int
    op_type: ChangeOpType
    node_handle: str
    node_title: str
    node_path: str
    actor_username: str
    created_at: datetime
    can_rollback: bool


class HistoryResponse(BaseModel):
    doc_id: str
    version: int
    items: list[HistoryItem]


class ChangeDetailResponse(BaseModel):
    change_id: int
    doc_id: str
    version_from: int
    version_to: int
    node_handle: str
    before_text: str
    after_text: str
    diff_html: str
    ai_instruction: str | None = None
    ai_explanation: str | None = None


class RollbackRequest(BaseModel):
    base_version: int = Field(ge=1)
    base_hash: NonEmptyStr


class RollbackResponse(BaseModel):
    doc_id: str
    rolled_back_change_id: int
    change_id: int
    version: int
    node_handle: str
    text_hash: str
```

### 8.4 `repository.py`

Repository only talks to DB. It must not read/write Markdown, workspace, or indexes. Prefer `flush()`/`refresh()` inside repository and let service decide `commit()`/`rollback()`.

Required public methods:

```python
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.modules.docedit.models import DocumentChange, DocumentEditState, NodeEditState


class DoceditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_document_state(self, doc_id: str) -> DocumentEditState | None:
        return await self.session.get(DocumentEditState, doc_id)

    async def get_or_create_document_state(self, doc_id: str) -> DocumentEditState:
        state = await self.get_document_state(doc_id)
        if state is not None:
            return state
        now = datetime.now(UTC)
        state = DocumentEditState(
            doc_id=doc_id,
            current_version=1,
            summary_status="clean",
            created_at=now,
            updated_at=now,
        )
        self.session.add(state)
        await self.session.flush()
        return state

    async def bump_document_version(
        self,
        state: DocumentEditState,
        *,
        summary_status: str = "stale",
    ) -> DocumentEditState:
        state.current_version += 1
        state.summary_status = summary_status
        state.updated_at = datetime.now(UTC)
        self.session.add(state)
        await self.session.flush()
        return state

    async def create_change(
        self,
        *,
        doc_id: str,
        version_from: int,
        version_to: int,
        node_handle: str,
        node_title: str,
        node_path: str,
        op_type: str,
        actor_user_id: int | None,
        actor_username: str,
        before_text: str,
        after_text: str,
        before_hash: str,
        after_hash: str,
        diff_html: str,
        ai_instruction: str | None = None,
        ai_explanation: str | None = None,
        source_change_id: int | None = None,
    ) -> DocumentChange:
        change = DocumentChange(
            doc_id=doc_id,
            version_from=version_from,
            version_to=version_to,
            node_handle=node_handle,
            node_title=node_title,
            node_path=node_path,
            op_type=op_type,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            before_text=before_text,
            after_text=after_text,
            before_hash=before_hash,
            after_hash=after_hash,
            diff_html=diff_html,
            ai_instruction=ai_instruction,
            ai_explanation=ai_explanation,
            source_change_id=source_change_id,
            created_at=datetime.now(UTC),
        )
        self.session.add(change)
        await self.session.flush()
        await self.session.refresh(change)
        return change

    async def get_change(self, change_id: int) -> DocumentChange | None:
        return await self.session.get(DocumentChange, change_id)

    async def list_changes(
        self,
        doc_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentChange]:
        stmt = (
            select(DocumentChange)
            .where(DocumentChange.doc_id == doc_id)
            .order_by(col(DocumentChange.version_to).desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.exec(stmt)
        return list(result.all())

    async def get_node_state(
        self,
        *,
        doc_id: str,
        node_handle: str,
    ) -> NodeEditState | None:
        stmt = select(NodeEditState).where(
            NodeEditState.doc_id == doc_id,
            NodeEditState.node_handle == node_handle,
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def list_changed_node_handles(self, doc_id: str) -> list[str]:
        stmt = select(NodeEditState.node_handle).where(
            NodeEditState.doc_id == doc_id,
            NodeEditState.changed == True,  # noqa: E712
        )
        result = await self.session.exec(stmt)
        return list(result.all())

    async def upsert_node_state(
        self,
        *,
        doc_id: str,
        node_handle: str,
        before_hash: str,
        after_hash: str,
        change_id: int,
    ) -> NodeEditState:
        state = await self.get_node_state(doc_id=doc_id, node_handle=node_handle)
        if state is None:
            state = NodeEditState(
                doc_id=doc_id,
                node_handle=node_handle,
                original_hash=before_hash,
                current_hash=after_hash,
                last_change_id=change_id,
                changed=after_hash != before_hash,
                summary_status="stale",
                updated_at=datetime.now(UTC),
            )
        else:
            state.current_hash = after_hash
            state.last_change_id = change_id
            state.changed = after_hash != state.original_hash
            state.summary_status = "stale"
            state.updated_at = datetime.now(UTC)
        self.session.add(state)
        await self.session.flush()
        await self.session.refresh(state)
        return state
```

Implementation details:

- `get_or_create_document_state()` creates version `1`, `summary_status="clean"`.
- `list_changes()` sorts by `version_to desc`.
- `list_changed_node_handles()` returns only rows with `changed == True`.
- `upsert_node_state()` keeps existing `original_hash`; it never overwrites it after the first edit.
- `create_change()` must `refresh(change)` so `change.id` is available before response creation.

### 8.5 `diff.py`

Service must generate safe diff HTML itself. Never trust LLM-provided HTML.

```python
from __future__ import annotations

import difflib
import hashlib
import html


def normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def text_hash(text: str) -> str:
    digest = hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def make_diff_html(before: str, after: str) -> str:
    before_norm = normalize_text(before)
    after_norm = normalize_text(after)
    matcher = difflib.SequenceMatcher(a=before_norm, b=after_norm)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(html.escape(before_norm[i1:i2]))
        elif tag == "delete":
            parts.append(f"<del>{html.escape(before_norm[i1:i2])}</del>")
        elif tag == "insert":
            parts.append(f"<ins>{html.escape(after_norm[j1:j2])}</ins>")
        elif tag == "replace":
            parts.append(f"<del>{html.escape(before_norm[i1:i2])}</del>")
            parts.append(f"<ins>{html.escape(after_norm[j1:j2])}</ins>")
    return "".join(parts)
```

Required tests:

- `test_text_hash_is_stable_and_prefixed`
- `test_text_hash_normalizes_line_endings`
- `test_make_diff_html_escapes_user_html`
- `test_make_diff_html_marks_delete_and_insert`

### 8.6 `workspace.py`

This is the most important correctness boundary. B2 must update Markdown, workspace JSON, catalog, and indexes consistently.

Required dataclasses:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class WorkspaceDocument:
    doc_id: str
    doc: dict[str, Any]
    ws_path: Path
    md_path: Path
    markdown: str


@dataclass(slots=True)
class LocatedNode:
    doc_id: str
    node_id: str
    handle: str
    node: dict[str, Any]
    ancestors: list[dict[str, Any]]
    all_nodes_by_line: list[dict[str, Any]]
    index_by_line: int
    next_node: dict[str, Any] | None


@dataclass(slots=True)
class MarkdownReplacement:
    markdown: str
    node_text: str
    line_delta: int
    line_count: int
    old_start_line: int
    old_end_line_exclusive: int


@dataclass(slots=True)
class ArtifactSnapshot:
    files: dict[Path, bytes | None]
    indexes_dir: dict | None
    pagemap_files: dict[Path, bytes | None]
```

Required functions and contracts:

- `parse_handle(handle: str) -> tuple[str, str]`: split `<doc_id>:<node_id>` and reject malformed handles.
- `load_workspace_doc(doc_id: str) -> WorkspaceDocument`: load workspace JSON and Markdown, validate `doc_id` and safe Markdown path.
- `locate_node(wdoc: WorkspaceDocument, node_id: str) -> LocatedNode`: find the node, ancestors, ordered nodes, and next node boundary.
- `extract_body_text(node_text: str) -> str`: remove exactly the first heading line and return body text.
- `build_node_text(heading_line: str, body_text: str) -> str`: create workspace `heading + body` text.
- `has_own_body_text(node_text: str) -> bool`: return true when extracted body contains non-heading body content.
- `classify_editable(handle: str, located: LocatedNode) -> tuple[bool, str | None]`: return `(False, reason)` for pure containers, empty nodes, active merged sections, and unknown nodes.
- `replace_node_body_in_markdown(wdoc, located, after_text) -> MarkdownReplacement`: replace only body lines after current heading and before next node heading.
- `apply_workspace_body_update(wdoc, located, repl) -> dict[str, Any]`: update node text, stale summary flags, document line count, and later node line numbers.
- `shift_pagemap_lines(wdoc, start_line_exclusive: int, line_delta: int) -> None`: shift Markdown line breakpoints whose old markdown line is `>= start_line_exclusive`. For an edit ending just before the next node, this is the old `end_idx + 1` one-based exclusive boundary.
- `snapshot_live_artifacts(wdoc) -> ArtifactSnapshot`: snapshot Markdown, workspace, pagemap, OCR metadata, catalog, workspace meta, domain dict, and indexes.
- `restore_live_artifacts(snapshot) -> None`: restore every path captured in the snapshot.
- `publish_workspace_edit_sync(doc_id, node_id, base_hash, after_text) -> dict`: under `INDEX_LOCK`, reload workspace, revalidate `base_hash`, prepare replacement, snapshot artifacts, write Markdown/workspace/pagemap atomically, and call `rebuild_from_workspace()`. This is synchronous and is the only helper wrapped by `run_in_threadpool()`.

Handle and document loading:

- `handle` format is `<doc_id>:<node_id>`.
- Reject missing colon, empty `doc_id`, or empty `node_id`.
- Load `settings.data_dir / "workspace" / f"{safe_name(doc_id)}.json"`.
- Validate `doc["id"] == doc_id`.
- Validate `doc["path"]` points under `settings.data_dir / "md"`.
- Read Markdown with UTF-8 and normalize `\r\n` / `\r` to `\n` before line calculations.

Node locating:

1. DFS traverse `doc["structure"]`.
2. Match `node["node_id"] == node_id`.
3. Collect ancestors.
4. Build `all_nodes_by_line` sorted by `line_num`.
5. `next_node` is the next node by `line_num`; it is the Markdown replacement boundary.

Own body text rule:

```python
def extract_body_text(node_text: str) -> str:
    lines = (node_text or "").split("\n")
    if lines and re.match(r"#{1,6}\s", lines[0].strip()):
        return "\n".join(lines[1:]).strip("\n")
    return (node_text or "").strip("\n")


def has_own_body_text(node_text: str) -> bool:
    return any(
        s and not re.match(r"#{1,6}\s", s)
        for s in (ln.strip() for ln in extract_body_text(node_text).split("\n"))
    )
```

Important: `has_children` does not mean uneditable. A node with children is editable when it has own body text before the first child.

Merged windows:

- Use `get_tree_store().read_node(handle)`.
- If `section.members` exists and length > 1, return `editable_reason="merged_section"`.
- v0 does not edit merged display nodes.

Markdown body replacement algorithm:

1. `lines = markdown.split("\n")`.
2. `start_idx = node["line_num"] - 1`.
3. Validate `doc["line_count"] == len(lines)`.
4. Validate `lines[start_idx]` is a Markdown heading line.
5. `end_idx = next_node["line_num"] - 1`, or `len(lines)` when no next node.
6. Extract `old_lines = lines[start_idx:end_idx]`.
7. Extract `old_block = "\n".join(old_lines).strip()`.
8. Validate normalized `old_block == node["text"].strip()`; otherwise reject as stale/ambiguous.
9. Validate old block body contains no Markdown heading lines. A body heading means the node boundary is ambiguous and B2 must reject the edit.
10. Preserve `heading_line = lines[start_idx]`.
11. Reject `after_text` if any line matches `^#{1,6}\s`; B2 must not create new headings.
12. New block is `[heading_line] + after_text.strip("\n").split("\n")`.
13. New Markdown is `lines[:start_idx] + new_block + lines[end_idx:]`.
14. New `node["text"]` is `"\n".join(new_block).strip()`.
15. Set `old_start_line = start_idx + 1` and `old_end_line_exclusive = end_idx + 1` using one-based line numbers. Pagemap entries with old line `>= old_end_line_exclusive` are after the edited block and must be shifted by `line_delta`.

Do not modify:

- title text.
- heading level.
- `node_id`.
- current node `line_num`.
- child `nodes`.
- child body text.
- `page` field.

Line number and pagemap updates:

- `line_delta = len(new_block) - len(old_lines)`.
- `doc["line_count"] = len(new_lines)`.
- Every node whose `line_num > current_node["line_num"]` gets `line_num += line_delta`.
- This includes child nodes after parent body and later siblings/chapters.
- Pagemap update uses the old exclusive boundary: shift entries with source markdown line `>= repl.old_end_line_exclusive` by `line_delta`. Do not shift lines inside the edited block.

Snapshot scope:

- current Markdown file.
- current `workspace/<doc_id>.json`.
- current Markdown `.pagemap.json` if present.
- current OCR sidecar/metadata files if present.
- `workspace/_meta.json`.
- `catalog/document_catalog.json`.
- `domain_dict_auto.txt`.
- whole `indexes/` directory.

Publish flow:

```python
from app.modules.ingest.locks import INDEX_LOCK
from app.modules.ingest.tree_service import rebuild_from_workspace

with INDEX_LOCK:
    # This synchronous function must reload workspace and validate again while
    # the formal artifact lock is held. Do not trust replacement data prepared
    # before waiting for INDEX_LOCK.
    if task_service.find_active_reparse(doc_id):
        raise DoceditConflict("active reparse")
    wdoc = load_workspace_doc(doc_id)
    located = locate_node(wdoc, node_id)
    before_body = extract_body_text(located.node["text"])
    if text_hash(before_body) != base_hash:
        raise DoceditConflict("node changed before publish")
    repl = replace_node_body_in_markdown(wdoc, located, after_text)
    updated_doc = apply_workspace_body_update(wdoc, located, repl)
    snapshot = snapshot_live_artifacts(wdoc)
    try:
        write_text_atomic(wdoc.md_path, repl.markdown)
        write_json_atomic(wdoc.ws_path, updated_doc, indent=2)
        if repl.line_delta:
            shift_pagemap_lines(
                wdoc,
                start_line_exclusive=repl.old_end_line_exclusive,
                line_delta=repl.line_delta,
            )
        rebuild_from_workspace()
        return {"index_rebuilt": True, "snapshot": snapshot, "before_body": before_body}
    except BaseException:
        restore_live_artifacts(snapshot)
        raise
```

Important: `publish_workspace_edit_sync()` does not commit DB. It catches publish/rebuild exceptions to restore its snapshot immediately, and it returns the same snapshot to `_save_node_internal()` so the service can restore again if DB flush/commit fails after publish succeeded.

Vector failure policy:

- Existing `rebuild_from_workspace()` treats vector rebuild as best-effort: BM25/catalog rebuild can succeed even if embedding/vector fails.
- B2 keeps that behavior for v0. A publish is successful when Markdown, workspace, pagemap, catalog, BM25, and DB version/history are consistent.
- If vector rebuild fails, do not rollback the B2 save solely for that reason. Retrieval must still work through BM25 fallback or previous vector artifacts.
- Add tests that monkeypatch vector build failure and assert save succeeds, BM25/catalog are published, and no stale exception is surfaced to the user.

Required forbidden calls in B2:

- `ingest_default()`
- `ingest_dir()`
- `ingest_one()`
- `build_tree()`
- `build_reparse_candidate()`
- `commit_reparse_candidate()`
- `build_vectors_default()` as the only publish step
- any write behavior in `/chat/node` or `TreeStore`

### 8.7 `agent.py` And Prompt

Follow B1 `docgen.agent` style:

- Call `settings.apply_litellm_env()` before constructing the client.
- `ChatOpenAI(model=settings.chat_model, base_url=settings.litellm_base_url, api_key=settings.litellm_api_key.get_secret_value(), temperature=0, timeout=..., max_retries=...)`.
- Mirror timeout/retry handling from `backend/app/modules/docgen/agent.py`.
- Parse JSON robustly.
- Validate with Pydantic.
- Do not call real LLM in unit tests.

Create `backend/app/modules/docedit/prompts/ai_edit.md`:

```markdown
你是火炬电子研发知识库的文档编辑助手。

你只允许修改【当前节点正文】。不要修改标题、章节层级、节点编号、子节点、其它章节或文档结构。

输出必须是 JSON 对象，不要使用 markdown 代码块：

{
  "after_text": "修改后的当前节点正文",
  "explanation": "一句话说明改了什么；如果没有改动，说明原因",
  "changed": true
}

规则：
1. 如果用户要求影响其它章节，只改当前节点中能安全对应的内容，并在 explanation 说明范围限制。
2. 如果指令不明确，保持 after_text 等于原文，changed=false。
3. 保留原文中明确的工艺参数、单位、表格语义，除非用户明确要求修改。
4. 不要输出 HTML。
5. 不要输出 diff；服务端会根据 before/after 生成 diff。

document:
{document}

node:
{node}

text:
{text}

instruction:
{instruction}
```

### 8.8 `service.py`

Exceptions:

```python
class DoceditForbidden(RuntimeError):
    pass


class DoceditNotFound(RuntimeError):
    pass


class DoceditConflict(RuntimeError):
    pass


class DoceditBadRequest(RuntimeError):
    pass


class DoceditUnprocessableEntity(RuntimeError):
    pass


class DoceditUpstreamError(RuntimeError):
    pass


class DoceditServiceUnavailable(RuntimeError):
    pass
```

Public function names and return contracts:

- `get_document_state(doc_id, *, user, session)` returns `DocumentStateResponse`.
- `get_node(handle, *, user, session)` returns `EditableNodeResponse`.
- `create_ai_diff(handle, req, *, user, session)` returns `AiDiffResponse`.
- `save_node(handle, req, *, user, session)` returns `SaveNodeResponse`.
- `list_history(doc_id, *, user, session)` returns `HistoryResponse`.
- `get_change(change_id, *, user, session)` returns `ChangeDetailResponse`.
- `rollback_change(change_id, req, *, user, session)` returns `RollbackResponse`.

Save order is fixed:

1. Parse handle. Malformed handle -> 400.
2. Require admin. Non-admin -> 403.
3. Acquire docedit save critical section with an `asyncio.Lock` or AnyIO async lock. Use one global `DOCEDIT_SAVE_LOCK` in v0, or a per-doc async lock if implemented without race-prone lock creation. Do not use `threading.RLock` as the docedit service lock.
4. Inside the lock, check active reparse before version/hash. Active reparse -> 409.
5. Inside the async lock, load document state and current node from the latest workspace for an early conflict check.
6. Check `base_version == state.current_version`. Mismatch -> 409.
7. Compute current `before_body = extract_body_text(workspace_node["text"])`.
8. Compute current `before_hash = text_hash(before_body)`.
9. Check `base_hash == before_hash`. Mismatch -> 409.
10. Check editability: own body text, not pure container, not merged section.
11. Normalize and validate body-only `after_text`.
12. Compute `after_hash = text_hash(after_text)` and server-side `diff_html`.
13. Create DB change row, bump version with `bump_document_version()`, upsert node state, and `flush()` only.
14. Run only `publish_workspace_edit_sync(doc_id, node_id, base_hash, after_text)` in `run_in_threadpool()`. That helper must acquire `INDEX_LOCK`, reload workspace, recheck document existence and `base_hash`, prepare Markdown/workspace/pagemap replacement, snapshot, write, shift pagemap, and rebuild indexes.
15. Commit DB.
16. If publish or DB flush/commit fails, rollback DB. If publish returned a snapshot and DB commit then fails, restore the returned artifact snapshot.
17. Add a concurrent save test proving two requests with the same `base_version/base_hash` cannot both create versions.

Private helper:

Create `_save_node_internal` returning `SaveNodeResponse` with these keyword-only arguments:

- `handle: str`
- `base_version: int`
- `base_hash: str`
- `after_text: str`
- `op_type: str`
- `user: CurrentUser`
- `session: AsyncSession`
- `ai_instruction: str | None = None`
- `ai_explanation: str | None = None`
- `source_change_id: int | None = None`

Its body is the fixed save order listed above. `save_node()` and `rollback_change()` must both call this helper so manual edit, AI accept, and rollback use the same Markdown/workspace/index/history update path.

`rollback_change()`:

- requires admin.
- loads target `DocumentChange`.
- checks active reparse.
- checks `base_version` and `base_hash`.
- rejects when current hash is not `target.after_hash`; this prevents overwriting later edits.
- calls `_save_node_internal(op_type="rollback", after_text=target.before_text, source_change_id=target.id)`.
- client request must not pass `op_type="rollback"` through `PATCH /nodes`; rollback is only an internal service path.

Delete/reparse/upload interaction:

- B2 save/rollback must hold the same formal-artifact critical section used around `INDEX_LOCK` writes.
- There is no async delete task state today. Delete is synchronous under `INDEX_LOCK`; if a document disappears while waiting for `INDEX_LOCK`, the publish helper's reload must detect it and return 404/409 before committing history.
- Active reparse statuses `queued`, `processing`, and `needs_review` block save, AI diff, and rollback.
- Statuses `done` and `failed` do not block.

`create_ai_diff()`:

- requires admin.
- checks active reparse.
- checks version/hash.
- checks editability.
- calls agent with only current node body text.
- does not write DB/files/indexes.
- returns server-generated diff.

### 8.9 `router.py`

Router only does dependency injection and error mapping.

```python
router = APIRouter(prefix="/docedit", tags=["docedit"])
```

Endpoints:

```python
GET  /docedit/documents/{doc_id}/state
GET  /docedit/nodes/{handle}
PATCH /docedit/nodes/{handle}
POST /docedit/nodes/{handle}/ai-diff
GET  /docedit/documents/{doc_id}/history
GET  /docedit/changes/{change_id}
POST /docedit/changes/{change_id}/rollback
```

Dependencies:

```python
user: CurrentUser = Depends(get_current_user)
session: AsyncSession = Depends(get_db)
```

Error mapping:

- `DoceditForbidden` -> 403.
- `DoceditNotFound` -> 404.
- `DoceditConflict` -> 409.
- `DoceditUnprocessableEntity` -> 422.
- `DoceditBadRequest` -> 400.
- `DoceditUpstreamError` -> 502.
- `DoceditServiceUnavailable` -> 503.

Behavior:

- `GET state` and `GET node` are available to normal users, but return `can_edit=false`.
- `PATCH`, `ai-diff`, and `rollback` require admin and return 403 otherwise.
- `{handle}` contains `doc_id:node_id`; frontend must use `encodeURIComponent(handle)`.

Register in `backend/app/main.py`:

```python
from app.modules.docedit.router import router as docedit_router  # noqa: E402

app.include_router(docedit_router)
```

### 8.10 Frontend Structure Modal Manual

Modify only `product/design/v0-mockup-v2.html`.

DOM:

- Add version badge next to `#tree-modal-title`; do not put it inside `h2` because existing code uses `textContent`.
- Add `#tree-history` button before `#tree-reparse`.
- Reuse `#tree-view` for both tree and history views; do not add a second main scroll container.

Required state near existing tree state:

```js
let treeViewMode = 'structure';
let currentTreeVersion = null;
let currentTreeCanEdit = false;
let currentTreeActiveReparse = false;
let currentTreeRoots = [];
let currentTreeKb = '';
const treeNodeUi = new Map();
const treeTouchedNodes = new Set();
```

Per-node UI state:

```js
{
  mode: 'view',
  text: '',
  textHash: '',
  baseVersion: 1,
  editable: false,
  editableReason: null,
  canEdit: false,
  pending: null,
  aiInstruction: '',
  opSeq: 0,
  error: ''
}
```

Tree root state:

- In `openDocTree()`, after applying the existing same-name root unwrap logic, assign the rendered roots to `currentTreeRoots`.
- `renderTreeStructure()` must rebuild `#tree-view` from `currentTreeRoots`; do not depend on the old DOM after history mode replaces `#tree-view.innerHTML`.

Required function names:

```js
async function loadTreeEditState(docId, requestSeq) {}
function updateTreeVersionBadge() {}
async function loadTreeNodeDetail(node, detail, requestSeq) {}
async function loadTreeNodeReadOnlyFromChat(node, detail, requestSeq) {}
function renderTreeNodeDetail(node, detail) {}
function setTreeNodeMode(handle, mode) {}
async function saveTreeNodeManual(handle) {}
function startTreeNodeAi(handle) {}
async function requestTreeNodeAiDiff(handle) {}
async function acceptTreeNodeAiDiff(handle) {}
function discardTreeNodeAiDiff(handle) {}
function refreshTreeAfterNodeSaved(handle, resp) {}
async function renderTreeHistory() {}
function toggleTreeHistory() {}
async function loadTreeChangeDiff(changeId, containerId) {}
async function rollbackTreeChange(changeId, nodeHandle) {}
function isTreeRequestCurrent(requestSeq, docId, handle, detail) {}
```

Keep existing text-display rule:

```js
const isSection = (node.windows || 1) > 1;
const showsText = !hasKids || isSection || !!node.has_text;
```

Button rule:

- `editable=true && can_edit=true && !currentTreeActiveReparse`: show "编辑" and "AI编辑".
- non-admin: no edit buttons.
- `hasKids && node.has_text && editable=true`: show buttons and a hint that only current node body is edited.
- pure container: expand/collapse only, no detail editor.
- `editable_reason="merged_section"`: show reason, no save button.

Request bodies:

```js
// manual save
{
  base_version: u.baseVersion,
  base_hash: u.textHash,
  after_text: textarea.value,
  op_type: 'manual_edit',
  ai_instruction: null,
  ai_explanation: null
}

// AI diff
{
  base_version: u.baseVersion,
  base_hash: u.textHash,
  instruction
}

// AI accept
{
  base_version: u.baseVersion,
  base_hash: u.textHash,
  after_text: u.pending.after_text,
  op_type: 'ai_edit',
  ai_instruction: u.aiInstruction,
  ai_explanation: u.pending.explanation || null
}
```

Stale async response guard:

```js
function isTreeRequestCurrent(requestSeq, docId, handle, detail){
  if(requestSeq !== treeOpenSeq) return false;
  if(currentTreeDocId !== docId) return false;
  if(!isTreeModalOpen()) return false;
  if(handle && detail && detail.dataset.handle !== handle) return false;
  return true;
}
```

Same-node operation guard:

```js
function ensureTreeNodeUi(handle){
  if(!treeNodeUi.has(handle)){
    treeNodeUi.set(handle, { mode:'view', text:'', textHash:'', baseVersion:currentTreeVersion || 1, editable:false, editableReason:null, canEdit:false, pending:null, aiInstruction:'', opSeq:0, error:'' });
  }
  return treeNodeUi.get(handle);
}

function beginTreeNodeOp(handle){
  const u = ensureTreeNodeUi(handle);
  u.opSeq = (u.opSeq || 0) + 1;
  return u.opSeq;
}

function isTreeNodeOpCurrent(handle, opSeq){
  const u = treeNodeUi.get(handle);
  return !!u && u.opSeq === opSeq;
}
```

Call `beginTreeNodeOp()` before node-level detail refresh, AI diff, save, accept, and rollback. After every await in those node-level operations, check both `isTreeRequestCurrent()` and `isTreeNodeOpCurrent()` before writing DOM/state.

Do not use node-level `opSeq` for document-level operations such as `openDocTree()`, `loadTreeEditState()`, or `renderTreeHistory()`; those use only `treeOpenSeq`, `currentTreeDocId`, and modal-open guards.

Use that guard after every await in:

- `loadTreeNodeDetail()`
- `loadTreeNodeReadOnlyFromChat()`
- `requestTreeNodeAiDiff()`
- `saveTreeNodeManual()`
- `acceptTreeNodeAiDiff()`
- `loadTreeChangeDiff()`
- `rollbackTreeChange()`

Use only document-level stale guards after every await in:

- `openDocTree()`
- `loadTreeEditState()`
- `renderTreeHistory()`

Existing `loaded` cache:

- Current `makeTreeNode()` has a closure-local `loaded` flag for body loading.
- B2 must not let that flag keep stale body HTML after save, AI accept, or rollback.
- Acceptable implementations:
  - remove the closure-local `loaded` cache and rely on `treeNodeUi`/request guards; or
  - after `refreshTreeAfterNodeSaved()` and rollback, rebuild the affected tree node/detail DOM so the old closure is discarded; or
  - explicitly store a per-handle revision and force reload when `currentTreeVersion` changes.

Diff HTML:

```js
function sanitizeDoceditDiff(html){
  if(window.DOMPurify){
    return DOMPurify.sanitize(String(html || ''), {
      ALLOWED_TAGS:['del','ins','br'],
      ALLOWED_ATTR:[]
    });
  }
  return escapeHtml(String(html || ''));
}
```

### 8.11 TDD And Tests

Before implementing, create tests first.

Repository tests:

- `test_get_or_create_document_state_initializes_version_one`
- `test_bump_document_version_updates_current_version`
- `test_create_change_appends_history_newest_first`
- `test_upsert_node_edit_state_tracks_changed_flag`
- `test_rollback_history_is_append_only`

Workspace tests:

- `test_parse_handle_returns_doc_and_node_id`
- `test_parse_handle_rejects_malformed_handle`
- `test_load_workspace_doc_rejects_md_path_outside_data_dir`
- `test_find_node_returns_node_and_ancestors`
- `test_has_own_body_text_ignores_heading_only_text`
- `test_replace_leaf_body_preserves_heading`
- `test_replace_non_leaf_node_with_own_text_does_not_touch_children`
- `test_pure_container_is_not_editable`
- `test_line_number_shift_updates_following_nodes`
- `test_pagemap_line_shift_updates_breakpoints_after_edited_block`
- `test_markdown_workspace_mismatch_is_rejected`
- `test_old_block_with_nested_heading_is_rejected`
- `test_snapshot_restore_restores_md_workspace_catalog_domain_dict_and_indexes`
- `test_snapshot_restore_restores_pagemap_and_ocr_sidecars`
- `test_publish_workspace_edit_restores_artifacts_when_rebuild_fails`

Diff/agent tests:

- `test_text_hash_is_stable_and_prefixed`
- `test_make_diff_html_escapes_user_html`
- `test_extract_json_object_from_fenced_json`
- `test_parse_ai_response_rejects_missing_after_text`
- `test_agent_uses_chat_model_from_settings_without_real_llm`

Service tests:

- `test_get_document_state_initializes_missing_state`
- `test_get_node_returns_editable_body_for_admin`
- `test_get_node_returns_can_edit_false_for_normal_user`
- `test_save_node_rejects_normal_user`
- `test_save_node_rejects_base_version_conflict`
- `test_save_node_rejects_base_hash_conflict`
- `test_save_node_rejects_pure_container`
- `test_save_node_accepts_non_leaf_node_with_own_text`
- `test_save_node_writes_markdown_workspace_history_and_rebuilds_index`
- `test_save_node_restores_snapshot_when_rebuild_fails`
- `test_save_node_restores_files_when_db_commit_fails_after_publish`
- `test_save_node_does_not_create_history_or_bump_version_when_publish_fails`
- `test_save_node_rejects_active_reparse_queued_processing_needs_review`
- `test_create_ai_diff_rejects_active_reparse_queued_processing_needs_review`
- `test_rollback_rejects_active_reparse_queued_processing_needs_review`
- `test_get_document_state_reports_active_reparse`
- `test_get_node_returns_active_reparse_reason`
- `test_create_ai_diff_does_not_write_files_db_or_indexes`
- `test_rollback_appends_new_change`
- `test_rollback_rejects_when_current_hash_differs_from_target_after_hash`
- `test_save_node_serializes_concurrent_same_base_hash_requests`
- `test_save_node_does_not_publish_when_db_flush_fails`
- `test_save_node_succeeds_when_vector_rebuild_fails_but_bm25_catalog_publish_succeeds`

Router tests:

- `test_get_document_state_endpoint_admin`
- `test_get_node_endpoint_normal_user_readonly`
- `test_patch_node_endpoint_rejects_normal_user`
- `test_patch_node_endpoint_maps_conflict`
- `test_patch_node_endpoint_saves_manual_edit`
- `test_ai_diff_endpoint_returns_preview`
- `test_history_endpoint_returns_timeline`
- `test_change_detail_endpoint_returns_diff`
- `test_rollback_endpoint_appends_version`
- `test_router_maps_not_found_bad_request_upstream_and_unavailable`

TDD commands:

```powershell
cd backend
uv run pytest tests/unit/docedit/test_repository.py -v
uv run pytest tests/unit/docedit/test_workspace.py -v
uv run pytest tests/unit/docedit/test_diff.py tests/unit/docedit/test_agent.py -v
uv run pytest tests/unit/docedit/test_service.py -v
uv run pytest tests/integration/test_docedit_router.py -v
```

Expected first failures:

- missing `app.modules.docedit`.
- missing SQLModel tables.
- `/docedit` router returns 404 before registration.

Mandatory DATA_DIR isolation:

- Every `tests/unit/docedit/*` and `tests/integration/test_docedit_router.py` fixture that touches workspace/service/router code must set `DATA_DIR` to `tmp_path`.
- Clear settings cache after changing env and before constructing services.
- Clear the process-level retrieval store cache with `clear_tree_store_for_tests()` after changing `DATA_DIR` and after the test yields.
- Assert `get_settings().data_dir == tmp_path` in the fixture so tests fail before touching real `backend/data`.
- Never read or write `backend/data` or `backend/data_eval` in docedit tests.

Example:

```python
from app.core.config import get_settings
from app.core.retrieval.store_provider import clear_tree_store_for_tests


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    clear_tree_store_for_tests()
    assert get_settings().data_dir == tmp_path
    yield tmp_path
    clear_tree_store_for_tests()
    get_settings.cache_clear()
```

Dependency override example:

```python
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.core.db import get_db


async def admin_user() -> CurrentUser:
    return CurrentUser(id=1, username="admin", is_admin=True)


async def normal_user() -> CurrentUser:
    return CurrentUser(id=2, username="normal", is_admin=False)


app.dependency_overrides[get_current_user] = admin_user
```

Router integration tests that use the real DB must also override `get_db` and clear overrides in `finally`, following `backend/tests/integration/test_ingest_router.py`:

```python
try:
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = admin_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ...
finally:
    app.dependency_overrides.clear()
```

Test doubles:

- Fake LLM returns fixed `after_text`, `explanation`, `changed`.
- Fake repository uses in-memory dicts and incrementing change ids.
- Workspace unit tests use real `tmp_path` files.
- Service tests can monkeypatch workspace publish and `rebuild_from_workspace`.
- Service tests must distinguish hard rebuild failure from vector fallback: hard `rebuild_from_workspace()` exceptions restore artifacts and fail save; vector build failure inside current `rebuild_from_workspace()` is best-effort and must not fail the B2 save when BM25/catalog publish succeeds.
- AI tests must not call real LiteLLM.

### 8.12 Manual Verification

After implementation:

```powershell
cd backend
uv run pytest tests/unit/docedit tests/integration/test_docedit_router.py -v
uv run pytest tests/unit/docgen tests/integration/test_docgen_router.py tests/integration/test_ingest_router.py tests/integration/test_ingest_pdf.py tests/integration/test_chat_router.py -v
uv run ruff check app/modules/docedit app/main.py tests/unit/docedit tests/integration/test_docedit_router.py
uv run pyright app/modules/docedit app/main.py
```

Frontend syntax:

```powershell
cd ..
node -e "const fs=require('fs');const s=fs.readFileSync('product/design/v0-mockup-v2.html','utf8');let n=0;for(const m of s.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)){new Function(m[1]);n++}console.log('checked',n,'scripts')"
```

Manual browser checks:

1. Open `http://localhost:8000/`.
2. Open a document structure modal.
3. Version badge appears when `/docedit` is available.
4. Leaf body renders as before.
5. Node with children and `has_text=true` shows its own body.
6. Pure container only expands/collapses.
7. Admin sees edit buttons; normal user does not.
8. Manual save increments version and shows "改过".
9. AI diff shows `<del>`/`<ins>` and accept increments version.
10. History view replaces `#tree-view` and can return to structure.
11. Rollback appends a new version and does not delete old history.
12. Closing or switching documents before requests return must not update the wrong modal.

Submit-time checks:

```powershell
git status --short
git diff --stat
git diff -- backend/app/modules/chat backend/app/modules/ingest backend/app/core/pageindex backend/app/core/retrieval/treestore.py
git diff -- backend/data backend/data_eval
git status --short -- backend/data backend/data_eval
git ls-files --others --exclude-standard backend/data backend/data_eval
```

Must not submit:

```text
backend/data/
backend/data_eval/
backend/data/md/
backend/data/workspace/
backend/data/catalog/
backend/data/indexes/
backend/data/docgen/sessions/*.json
__pycache__/
*.pyc
.env
```

---

## 9. Full Verification

Run targeted tests first:

```powershell
cd backend
uv run pytest tests/unit/docedit tests/integration/test_docedit_router.py -v
```

Then run affected existing tests:

```powershell
cd backend
uv run pytest tests/unit/docgen tests/integration/test_docgen_router.py tests/unit/retrieval -v
uv run pytest tests/integration/test_ingest_router.py tests/integration/test_ingest_pdf.py tests/integration/test_chat_router.py -v
uv run pytest tests/unit/test_hybrid_search.py tests/unit/test_search_nodes.py tests/unit/test_treestore.py tests/unit/test_vector_index.py -v
uv run pytest -v
```

Static checks:

```powershell
cd backend
uv run ruff check app/modules/docedit app/main.py tests/unit/docedit tests/integration/test_docedit_router.py
uv run pyright app/modules/docedit app/main.py
```

Frontend script syntax:

```powershell
cd ..
node -e "const fs=require('fs');const s=fs.readFileSync('product/design/v0-mockup-v2.html','utf8');let n=0;for(const m of s.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)){new Function(m[1]);n++}console.log('checked',n,'scripts')"
```

Manual DB/file consistency check after one test edit:

- Workspace node text changed.
- Markdown body changed.
- `document_catalog.json` mtime changed.
- `TreeStore.read_node(handle)` returns new text.
- B1 `read_parsed_document(doc_id).markdown` contains new text.
- History has a new version.
- Rollback creates another new version and restores text.

---

## 10. Common Failure Modes And Required Handling

- **User is not admin:** no edit buttons in frontend; backend still returns 403 if called directly.
- **Auth placeholder returns non-admin:** local manual testing needs a way to inject/admin-enable current user. Do not bypass backend permission permanently.
- **Active reparse:** save/AI/rollback returns 409; frontend shows "该文档正在重新解析，暂不能编辑".
- **Base version conflict:** return 409 and ask frontend to refresh current node.
- **Base hash conflict:** return 409; do not overwrite current text.
- **Merged section/window:** reject v0 edit with a clear reason unless implementation can map and update every member correctly.
- **Markdown replacement ambiguous:** reject and do not update workspace only.
- **Hard index rebuild fails:** restore files/indexes/catalog to snapshot.
- **Vector rebuild fails:** follow existing best-effort vector policy. Do not fail the save solely because vector embedding/index build failed if BM25/catalog were published successfully.
- **AI invalid JSON:** return 502; keep current node unchanged.
- **AI returns HTML:** treat as text or reject; server creates diff HTML itself.
- **Summary still stale:** retrieval remains usable with updated body text; UI may show summary pending. Later regeneration must publish indexes again.
- **DOMPurify strips diff tags:** configure or post-process to allow only `del`/`ins`.
- **TreeStore stale cache:** ensure catalog or vector metadata mtime changes through `rebuild_from_workspace()`.
- **History rollback after later edits:** reject with 409 unless current hash equals target `after_hash`.

---

## 11. PRD Coverage Checklist

- [ ] B2 not new page: implemented inside `#tree-modal`.
- [ ] Admin-only editing: backend enforced.
- [ ] Normal user read-only: no edit controls and backend 403.
- [ ] Manual edit: textarea save/cancel.
- [ ] AI edit: instruction input, preview diff, accept/retry/discard.
- [ ] AI scope: only current node own text.
- [ ] Non-leaf with own text: body shown and editable if backend says editable.
- [ ] Pure container: not editable.
- [ ] Immediate formal update: Markdown + workspace + indexes updated.
- [ ] Document-level version increments.
- [ ] Node "改过" marker.
- [ ] Document-level history timeline.
- [ ] Diff detail.
- [ ] Rollback creates new version.
- [ ] Active reparse conflict handled.
- [ ] Summary stale/regeneration tracked.
- [ ] No full PDF reparse for B2 edit.

---

## 12. Commit Strategy

Only commit when the user asks for commit/push.

Recommended split:

1. Backend B2 edit module:

```powershell
git add backend/app/main.py backend/app/modules/docedit backend/tests/unit/docedit backend/tests/integration/test_docedit_router.py
git commit -m "[T-B2] feat: 新增B2文档模块编辑后端能力"
```

2. Frontend B2 structure modal:

```powershell
git add product/design/v0-mockup-v2.html
git commit -m "[T-B2] feat: 接入B2结构弹窗内编辑交互"
```

3. Plan document, if committing the plan:

```powershell
git add docs/plans/2026-07-02-b2-module-edit-implementation-plan.md
git commit -m "[T-B2] docs: 补充B2模块编辑实施计划"
```

Before push:

```powershell
git status --short
git log --oneline -3
```

Do not include:

```text
backend/data_eval/
backend/data/docgen/sessions/*.json
backend/data/md/
backend/data/workspace/
backend/data/catalog/
backend/data/indexes/
```
