from app.core.vlm.schemas import NotsureSpan


def parse_notsure_segments(text: str) -> list[NotsureSpan]:
    """解析 <notsure>...</notsure> 标记，返回所有片段。

    嵌套时取最外层（栈式扫描）；多个独立段返回多段。
    """
    if "<notsure>" not in text:
        return []

    segments: list[NotsureSpan] = []
    stack: list[int] = []
    pos = 0
    open_tag = "<notsure>"
    close_tag = "</notsure>"

    while pos < len(text):
        next_open = text.find(open_tag, pos)
        next_close = text.find(close_tag, pos)

        if next_open == -1 and next_close == -1:
            break

        if next_open != -1 and (next_close == -1 or next_open < next_close):
            stack.append(next_open)
            pos = next_open + len(open_tag)
        else:
            if stack:
                start = stack.pop()
                if not stack:  # 最外层闭合
                    end = next_close + len(close_tag)
                    inner = text[start + len(open_tag) : next_close]
                    segments.append(NotsureSpan(start=start, end=end, text=inner))
            pos = next_close + len(close_tag)

    return segments
