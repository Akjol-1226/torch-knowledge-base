import re

_RE_PROJECT_ID = re.compile(r"\bNPD\d{4,6}\b", re.IGNORECASE)
_RE_REPORT_ID = re.compile(r"\bHJ\d{5,8}\b", re.IGNORECASE)
_RE_DRAWING_ID = re.compile(r"\b[A-Za-z]{1,4}\d+(?:\.\d+){2,}(?:-\d+)?(?:\.[A-Za-z]{2,6})?\b")
# 型号：字母数字混排，允许内部 - 与 "数字.数字"（如 0.10n），结尾不吃孤立的句末点
_RE_PRODUCT_MODEL = re.compile(
    r"\b[A-Za-z]{1,6}\d{1,6}(?:[A-Za-z\d\-]|\.\d)*[A-Za-z\d]\b"
)
_MODEL_MIN_LEN = 4


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _spacing(text: str) -> str:
    """Insert spaces between ASCII alnum and non-ASCII chars so \\b works near CJK."""
    text = re.sub(r'([A-Za-z0-9])([^\x00-\x7F])', r'\1 \2', text)
    text = re.sub(r'([^\x00-\x7F])([A-Za-z0-9])', r'\1 \2', text)
    return text


def extract_identifiers(text: str) -> dict:
    text = _spacing(text)
    projects = _uniq(m.group(0).upper() for m in _RE_PROJECT_ID.finditer(text))
    reports = _uniq(m.group(0).upper() for m in _RE_REPORT_ID.finditer(text))
    drawings = _uniq(m.group(0) for m in _RE_DRAWING_ID.finditer(text))
    models = _uniq(
        m.group(0) for m in _RE_PRODUCT_MODEL.finditer(text)
        if len(m.group(0)) >= _MODEL_MIN_LEN
    )
    # 去掉与项目/报告/图号重复的型号误抽
    overlap = set(projects) | set(reports) | set(drawings)
    models = [m for m in models if m.upper() not in {o.upper() for o in overlap}]
    return {"project_ids": projects, "report_ids": reports,
            "drawing_ids": drawings, "product_models": models}
