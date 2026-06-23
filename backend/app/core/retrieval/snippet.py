from .tokenize import tokenize


def make_snippet(text: str, query: str, width: int = 60) -> str:
    text = text or ""
    if not text:
        return ""
    terms = [t for t in tokenize(query) if t.strip()]
    pos = -1
    for t in terms:
        p = text.find(t)
        if p != -1:
            pos = p
            break
    if pos == -1:
        return text[:width]
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    snip = text[start:end]
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip = snip + "…"
    return snip
