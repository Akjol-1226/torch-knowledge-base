import re
from pathlib import Path

try:
    import jieba
    _JIEBA = True
except Exception:
    jieba = None
    _JIEBA = False


def _read_existing(dict_path: Path) -> set:
    if not dict_path.exists():
        return set()
    out = set()
    for line in dict_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # 本项目词典用 TAB 分列；第一列即词本身（不会被型号内空格/点破坏）
        out.add(line.split("\t")[0])
    return out


def write_domain_terms(terms, dict_path: Path) -> None:
    """追加去重写 jieba 词典。格式：`词<TAB>freq<TAB>pos`，避免空格分列损坏含点/连字符的型号。"""
    dict_path = Path(dict_path)
    dict_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_existing(dict_path)
    new = []
    for t in terms:
        t = t.strip()
        if not t or " " in t:        # 含空格的不进词典（型号本不该有空格）
            continue
        if t not in existing:
            existing.add(t)
            new.append(t)
    if new:
        with dict_path.open("a", encoding="utf-8") as f:
            for t in new:
                f.write(f"{t}\t10\tn\n")
    if jieba is not None:
        for t in new:
            jieba.add_word(t, freq=10, tag="n")   # 强制整词，不依赖文件格式解析


def load_dict(dict_path: Path) -> int:
    """把域词典里每个词强制注册为 jieba 整词（add_word），返回加载词数。

    不走 jieba.load_userdict：它按空格解析、且不会把含 `-`/`.` 的型号保留为整词，
    会把 `E2-326` 切成 ['E2','-','326']。serving 进程必须调用本函数，否则查询端
    分词与入库端不一致，型号/图号搜不到。容忍 TAB 或空格分隔（只取第一列）。"""
    if jieba is None:
        return 0
    dict_path = Path(dict_path)
    if not dict_path.exists():
        return 0
    count = 0
    for line in dict_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        term = re.split(r"[\t ]", line, maxsplit=1)[0].strip()
        if term:
            jieba.add_word(term, freq=10, tag="n")
            count += 1
    return count


def tokenize(text: str) -> list:
    if jieba is not None:
        return list(jieba.cut(text, cut_all=False, HMM=True))
    return re.split(r"\s+", text.strip())
