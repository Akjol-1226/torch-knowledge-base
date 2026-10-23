"""Content-bound evaluation release from an OS-leased, static staging copy.

This profile deliberately rebuilds a common BM25-only baseline. Historical
hybrid indexes are neither reused nor certified. The source receipt describes
the capture program's lease contract; it is not cryptographic authentication.
"""

from __future__ import annotations

import copy
import hashlib
import io
import re
import stat
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any

import bm25s
import jieba
import jieba.finalseg
import numpy as np

from app.core.retrieval.bm25_index import CONTEXT_BOOST, SUMMARY_BOOST, TITLE_BOOST, BM25Index
from app.core.retrieval.nodes import iter_nodes
from app.core.retrieval.treestore import TreeStore
from evals.agent_artifacts import strict_json
from evals.agent_schemas import canonical_json, canonical_sha256

PROFILE = "bm25_only_rebuilt_common_baseline"
RECEIPT = "source-capture.json"
MAX_FILES = 10_000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes) -> Any:
    try:
        return strict_json(data.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("release input is not UTF-8") from exc


def _bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _positive(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("positive exact integer required")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonblank text required")
    return value


def _relative(value: Any) -> str:
    value = _text(value)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(
            part in {".", ".."}
            or "\\" in part
            or ":" in part
            or part.endswith((".", " "))
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
            for part in path.parts
        )
    ):
        raise ValueError("unsafe relative file path")
    return value


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _read_tree(root: Path) -> dict[str, bytes]:
    if ".." in root.parts:
        raise ValueError("release root traverses a parent")
    root = root.absolute()
    if any(_is_link(path) for path in (root, *root.parents)) or not root.is_dir():
        raise ValueError("release root must be an ordinary directory")
    files: dict[str, bytes] = {}
    folded: set[str] = set()
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            if _is_link(path):
                raise ValueError("links and reparse points are forbidden")
            relative = _relative(path.relative_to(root).as_posix())
            if relative.casefold() in folded:
                raise ValueError("case-ambiguous file path")
            folded.add(relative.casefold())
            if path.is_dir():
                pending.append(path)
                continue
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError("only ordinary, unlinked files are allowed")
            if len(files) >= MAX_FILES:
                raise ValueError("too many release files")
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("release size limit exceeded")
            files[relative] = data
    return files


def _md_relative(raw: Any, origin: Path) -> str:
    raw = _text(raw)
    path = Path(raw)
    if ".." in path.parts:
        raise ValueError("workspace MD path traverses a parent")
    if path.is_absolute():
        try:
            relative = path.relative_to(origin).as_posix()
        except ValueError as exc:
            raise ValueError("workspace MD path escapes declared source root") from exc
    else:
        relative = path.as_posix()
    relative = _relative(relative)
    if not relative.startswith("md/") or not relative.endswith(".md"):
        raise ValueError("workspace must reference a staged MD")
    return relative


def _nonblank_lines(text: str) -> list[str]:
    # Existing PageIndex thinning strips block edges and rejoins children with
    # blank lines. Preserve every nonblank line and its order, not word overlap.
    return [line for line in text.strip().split("\n") if line.strip()]


def _flatten(structure: Any) -> tuple[list[dict], dict[str, str | None]]:
    nodes: list[dict] = []
    ids: set[str] = set()
    parents: dict[str, str | None] = {}

    def walk(children: Any, parent: str | None = None) -> None:
        if not isinstance(children, list):
            raise ValueError("invalid tree structure")
        for node in children:
            if not isinstance(node, dict):
                raise ValueError("invalid tree node")
            node_id = _text(node.get("node_id"))
            if node_id in ids or ":" in node_id:
                raise ValueError("duplicate or ambiguous node ID")
            ids.add(node_id)
            parents[node_id] = parent
            _text(node.get("title"))
            summary = node.get("summary")
            if not isinstance(node.get("text"), str) or (
                summary is not None and not isinstance(summary, str)
            ):
                raise ValueError("node text and summary must be strings")
            _positive(node.get("line_num"))
            nodes.append(node)
            walk(node.get("nodes", []), node_id)

    walk(structure)
    return nodes, parents


def _documents(files: dict[str, bytes], receipt: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    origin = Path(_text(receipt.get("source_data_dir")))
    if not origin.is_absolute() or ".." in origin.parts:
        raise ValueError("source root must be absolute")
    catalog = _json(files["catalog/document_catalog.json"])
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("catalog must contain documents")
    cards: dict[str, dict] = {}
    for card in catalog:
        if not isinstance(card, dict):
            raise ValueError("invalid catalog card")
        identity = _text(card.get("doc_id"))
        if identity in cards:
            raise ValueError("duplicate catalog document")
        cards[identity] = card
    docs = []
    used_md: set[str] = set()
    for name, raw in sorted(files.items()):
        if not name.startswith("workspace/doc_") or not name.endswith(".json"):
            continue
        doc = _json(raw)
        if not isinstance(doc, dict):
            raise ValueError("invalid workspace document")
        identity = _text(doc.get("id"))
        if name != f"workspace/{identity}.json" or identity not in cards:
            raise ValueError("workspace and catalog identities differ")
        card = cards[identity]
        for key in ("doc_name", "doc_description", "kb"):
            default = "default" if key == "kb" else ""
            if not isinstance(doc.get(key, default), str) or doc.get(key, default) != card.get(
                key, default
            ):
                raise ValueError("catalog and workspace metadata differ")
        md_path = _md_relative(doc.get("path"), origin)
        if md_path in used_md or md_path not in files:
            raise ValueError("missing or multiply bound MD source")
        used_md.add(md_path)
        try:
            markdown = files[md_path].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError as exc:
            raise ValueError("MD source is not UTF-8") from exc
        lines = markdown.split("\n")
        if _positive(doc.get("line_count")) != len(lines):
            raise ValueError("workspace and MD line counts differ")
        nodes, parents = _flatten(doc.get("structure"))
        if not nodes or any(
            node["line_num"] > len(lines)
            or (index > 0 and node["line_num"] <= nodes[index - 1]["line_num"])
            for index, node in enumerate(nodes)
        ):
            raise ValueError("tree node lines are not strictly ordered")
        if any(line.strip() for line in lines[: nodes[0]["line_num"] - 1]):
            raise ValueError("nonblank MD preamble has no indexed node")
        ancestry: list[tuple[int, str]] = []
        for index, node in enumerate(nodes):
            header = re.fullmatch(r"(#{1,6})\s+(.+)", lines[node["line_num"] - 1].strip())
            if header is None or header[2].strip() != node["title"]:
                raise ValueError("node title differs from its MD heading")
            level = len(header[1])
            while ancestry and ancestry[-1][0] >= level:
                ancestry.pop()
            if parents[node["node_id"]] != (ancestry[-1][1] if ancestry else None):
                raise ValueError("tree parent differs from MD heading hierarchy")
            ancestry.append((level, node["node_id"]))
            end = nodes[index + 1]["line_num"] - 1 if index + 1 < len(nodes) else len(lines)
            expected = "\n".join(lines[node["line_num"] - 1 : end])
            if _nonblank_lines(node["text"]) != _nonblank_lines(expected):
                raise ValueError("workspace node text differs from its MD range")
        frozen = copy.deepcopy(doc)
        frozen["path"] = md_path
        docs.append(frozen)
    if {doc["id"] for doc in docs} != set(cards):
        raise ValueError("catalog and workspace document sets differ")
    if {name for name in files if name.startswith("md/") and name.endswith(".md")} != used_md:
        raise ValueError("unindexed MD source in staging")
    if "workspace/_meta.json" in files:
        meta = _json(files["workspace/_meta.json"])
        if not isinstance(meta, dict) or set(meta) != set(cards):
            raise ValueError("workspace metadata document set differs")
        for doc in docs:
            item = meta[doc["id"]]
            if not isinstance(item, dict) or _md_relative(item.get("path"), origin) != doc["path"]:
                raise ValueError("workspace metadata MD binding differs")
            for key in ("doc_name", "doc_description", "line_count", "kb"):
                default = "default" if key == "kb" else ""
                if canonical_json(item.get(key, default)) != canonical_json(doc.get(key, default)):
                    raise ValueError("workspace metadata differs")
    return docs, catalog


def _source(files: dict[str, bytes]) -> tuple[dict, list[dict], list[dict]]:
    if RECEIPT not in files:
        raise ValueError("source capture receipt missing")
    receipt = _json(files[RECEIPT])
    if (
        not isinstance(receipt, dict)
        or type(receipt.get("source_capture_schema_version")) is not int
    ):
        raise ValueError("invalid source capture receipt")
    if receipt["source_capture_schema_version"] != 1 or (
        receipt.get("capture_method") != "windows_read_handles_deny_write_delete"
        or receipt.get("all_handles_held_during_read") is not True
    ):
        raise ValueError("source receipt lacks the required OS lease contract")
    hashes = receipt.get("files")
    if not isinstance(hashes, dict) or set(hashes) != set(files) - {RECEIPT}:
        raise ValueError("staging files differ from captured file set")
    for name, digest in hashes.items():
        _relative(name)
        if not isinstance(digest, str) or not _SHA.fullmatch(digest) or _sha(files[name]) != digest:
            raise ValueError("captured staging file hash mismatch")
        allowed = name in {
            "catalog/document_catalog.json",
            "domain_dict_auto.txt",
            "workspace/_meta.json",
        }
        allowed |= (
            name.startswith("workspace/doc_") and name.endswith(".json") and name.count("/") == 1
        )
        allowed |= name.startswith("md/") and name.endswith(".md")
        if not allowed:
            raise ValueError("unexpected source file or historical index")
    if not {"catalog/document_catalog.json", "domain_dict_auto.txt"}.issubset(hashes):
        raise ValueError("required source file missing")
    docs, catalog = _documents(files, receipt)
    return receipt, docs, catalog


class _LocalBM25(BM25Index):
    def __init__(self, records: list[dict], dictionary: bytes):
        if jieba.finalseg.Force_Split_Words:
            raise ValueError("global jieba HMM splitting state is not clean")
        self.tokenizer = jieba.Tokenizer()
        self.tokenizer.initialize()
        for line in dictionary.decode("utf-8").splitlines():
            term = re.split(r"[\t ]", line.strip(), maxsplit=1)[0].strip()
            if term:
                self.tokenizer.add_word(term, freq=10, tag="n")
        corpus = []
        for rec in records:
            corpus.append(
                self.tokens(rec["title"]) * TITLE_BOOST
                + self.tokens(rec.get("context", "")) * CONTEXT_BOOST
                + self.tokens(rec["summary"]) * SUMMARY_BOOST
                + self.tokens(rec["text"])
            )
        unique = {token for tokens in corpus for token in tokens}
        unique.add("")
        vocabulary = {token: index for index, token in enumerate(sorted(unique))}
        ids = [[vocabulary[token] for token in tokens] for tokens in corpus]
        self.retriever = bm25s.BM25()
        self.retriever.index((ids, vocabulary), show_progress=False)
        self.meta = [
            {"node_id_full": rec["node_id_full"], "title": rec["title"]} for rec in records
        ]
        super().__init__(self.retriever, self.meta)

    def tokens(self, text: str) -> list[str]:
        if jieba.finalseg.Force_Split_Words:
            raise ValueError("global jieba HMM splitting state changed")
        return list(self.tokenizer.cut(text, cut_all=False, HMM=True))

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        tokens = self.tokens(query)
        if not tokens:
            return []
        results, scores = self.retriever.retrieve(
            [tokens], k=min(top_k, len(self.meta)), return_as="tuple", show_progress=False
        )
        return [
            {**self.meta[int(index)], "score": float(score)}
            for index, score in zip(results[0], scores[0], strict=True)
        ]

    def files(self) -> dict[str, bytes]:
        result = {
            "meta.json": _bytes(self.meta),
            "bm25/vocab.index.json": _bytes(self.retriever.vocab_dict),
        }
        for key in ("data", "indices", "indptr"):
            stream = io.BytesIO()
            np.save(stream, self.retriever.scores[key], allow_pickle=False)
            result[f"bm25/{key}.csc.index.npy"] = stream.getvalue()
        result["bm25/params.index.json"] = _bytes(
            {
                "k1": self.retriever.k1,
                "b": self.retriever.b,
                "delta": self.retriever.delta,
                "method": self.retriever.method,
                "idf_method": self.retriever.idf_method,
                "dtype": self.retriever.dtype,
                "int_dtype": self.retriever.int_dtype,
                "num_docs": len(self.meta),
                "version": version("bm25s"),
            }
        )
        return result


def _software() -> dict[str, str]:
    return {name: version(name) for name in ("bm25s", "jieba", "numpy")}


def _implementation() -> dict[str, str]:
    backend = Path(__file__).resolve().parents[1]
    paths = [Path(__file__).resolve(), *sorted((backend / "app/core/retrieval").glob("*.py"))]
    return {path.relative_to(backend).as_posix(): _sha(path.read_bytes()) for path in paths}


def _data(
    files: dict[str, bytes], docs: list[dict], catalog: list[dict]
) -> tuple[dict[str, bytes], _LocalBM25]:
    records = [record for doc in docs for record in iter_nodes(doc["id"], doc)]
    index = _LocalBM25(records, files["domain_dict_auto.txt"])
    output = {name: raw for name, raw in files.items() if name.startswith("md/")}
    output["domain_dict_auto.txt"] = files["domain_dict_auto.txt"]
    output["catalog/document_catalog.json"] = _bytes(catalog)
    for doc in docs:
        output[f"workspace/{doc['id']}.json"] = _bytes(doc)
    output.update({f"indexes/{name}": raw for name, raw in index.files().items()})
    return output, index


def create_release(staging: Path, output: Path, *, retrieval_top_n: int) -> dict[str, Any]:
    """Build a new, explicit common BM25 baseline; never read the live source root."""
    _positive(retrieval_top_n)
    if ".." in staging.parts or ".." in output.parts:
        raise ValueError("staging and output roots must not traverse parents")
    staging, output = staging.absolute(), output.absolute()
    if staging == output or staging in output.parents or output in staging.parents:
        raise ValueError("staging and release paths must be separate")
    source = _read_tree(staging)
    receipt, docs, catalog = _source(source)
    data, _ = _data(source, docs, catalog)
    artifacts = {f"source/{name}": raw for name, raw in source.items()}
    artifacts.update({f"data/{name}": raw for name, raw in data.items()})
    manifest = {
        "release_schema_version": 1,
        "retrieval_profile": PROFILE,
        "source_capture_sha256": _sha(source[RECEIPT]),
        "source_capture_method": receipt["capture_method"],
        "source_receipt_is_authentication": False,
        "historical_indexes": "discarded_not_reused_or_validated",
        "vector_policy": "disabled_for_both_arms",
        "retrieval_top_n": retrieval_top_n,
        "tokenizer_policy": "isolated_jieba_frozen_domain_dict_sorted_vocabulary",
        "text_validation": "exact_nonblank_MD_lines_known_thinning_separators",
        "software": _software(),
        "implementation": _implementation(),
        "documents": len(docs),
        "nodes": sum(1 for doc in docs for _ in iter_nodes(doc["id"], doc)),
        "files": {name: _sha(raw) for name, raw in sorted(artifacts.items())},
    }
    manifest["release_id"] = canonical_sha256(manifest)
    manifest_bytes = _bytes(manifest)
    if (
        len(artifacts) + 1 > MAX_FILES
        or sum(map(len, artifacts.values())) + len(manifest_bytes) > MAX_TOTAL_BYTES
    ):
        raise ValueError("generated release exceeds size limit")
    for ancestor in output.parents:
        if ancestor.exists() and _is_link(ancestor):
            raise ValueError("release output ancestor is a link or reparse point")
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in artifacts.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (output / "manifest.json").write_bytes(manifest_bytes)
    return manifest


def _load(root: Path) -> tuple[dict, list[dict], list[dict], _LocalBM25]:
    files = _read_tree(root)
    if "manifest.json" not in files:
        raise ValueError("release manifest missing")
    manifest = _json(files.pop("manifest.json"))
    if not isinstance(manifest, dict) or type(manifest.get("release_schema_version")) is not int:
        raise ValueError("invalid release manifest")
    identity = manifest.get("release_id")
    if identity != canonical_sha256(
        {key: value for key, value in manifest.items() if key != "release_id"}
    ):
        raise ValueError("release identity mismatch")
    if (
        manifest.get("release_schema_version") != 1
        or manifest.get("retrieval_profile") != PROFILE
        or (
            manifest.get("vector_policy") != "disabled_for_both_arms"
            or manifest.get("software") != _software()
            or manifest.get("implementation") != _implementation()
        )
    ):
        raise ValueError("unsupported release profile or software")
    if (
        manifest.get("source_capture_method") != "windows_read_handles_deny_write_delete"
        or manifest.get("source_receipt_is_authentication") is not False
        or manifest.get("historical_indexes") != "discarded_not_reused_or_validated"
        or manifest.get("tokenizer_policy") != "isolated_jieba_frozen_domain_dict_sorted_vocabulary"
        or manifest.get("text_validation") != "exact_nonblank_MD_lines_known_thinning_separators"
    ):
        raise ValueError("invalid release provenance contract")
    _positive(manifest.get("retrieval_top_n"))
    _positive(manifest.get("documents"))
    _positive(manifest.get("nodes"))
    hashes = manifest.get("files")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != set(files)
        or any(
            not isinstance(digest, str) or not _SHA.fullmatch(digest) or _sha(files[name]) != digest
            for name, digest in hashes.items()
        )
    ):
        raise ValueError("release file set or hash mismatch")
    source = {
        name.removeprefix("source/"): raw
        for name, raw in files.items()
        if name.startswith("source/")
    }
    _, docs, catalog = _source(source)
    if manifest.get("source_capture_sha256") != _sha(source[RECEIPT]):
        raise ValueError("source receipt binding mismatch")
    data, index = _data(source, docs, catalog)
    expected = {f"data/{name}": raw for name, raw in data.items()}
    actual = {name: raw for name, raw in files.items() if not name.startswith("source/")}
    if expected != actual:
        raise ValueError("rebuilt release data or BM25 differs from frozen sources")
    if manifest.get("documents") != len(docs) or manifest.get("nodes") != len(index.meta):
        raise ValueError("release counts differ from source records")
    return manifest, docs, catalog, index


def audit_release(root: Path) -> dict[str, Any]:
    """Independently rebuild from verified buffers; reject stale or altered indexes."""
    return _load(root)[0]


class FrozenReadView:
    """Five tool-facing reads over verified memory; no refresh or live-file lookup."""

    def __init__(self, manifest: dict, docs: list[dict], catalog: list[dict], index: _LocalBM25):
        self.release_id = manifest["release_id"]
        self.retrieval_profile = PROFILE
        self._top_n = manifest["retrieval_top_n"]
        store = TreeStore.__new__(TreeStore)
        store._catalog, store._docs = copy.deepcopy(catalog), {}
        store._nodes, store._top, store._doc_handles, store._doc_mtime = {}, {}, {}, {}
        store._bm25, store._vec = index, None
        for doc in docs:
            store._docs[doc["id"]] = doc
            store._index_doc(doc)
        self._store = store

    def list_catalog(self, kbs=None):
        return copy.deepcopy(self._store.list_catalog(kbs=kbs))

    def get_outline(self, doc_id):
        return copy.deepcopy(self._store.get_outline(doc_id))

    def open_node(self, node_id):
        return copy.deepcopy(self._store.open_node(node_id))

    def read_node(self, node_id):
        return copy.deepcopy(self._store.read_node(node_id))

    def search_nodes(self, query, top_k=8, kbs=None):
        from app.core.retrieval.treestore import get_settings

        if get_settings().retrieval_top_n != self._top_n:
            raise ValueError("retrieval_top_n differs from frozen release")
        return copy.deepcopy(self._store.search_nodes(query, top_k=top_k, kbs=kbs))


def load_release(root: Path) -> FrozenReadView:
    """Load one reader from the same byte buffers that passed independent audit."""
    manifest, docs, catalog, index = _load(root)
    return FrozenReadView(manifest, docs, catalog, index)
