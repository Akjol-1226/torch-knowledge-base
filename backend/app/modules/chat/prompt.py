"""对话系统提示词。

正文外置到 `prompts/system.md`——是版本化资产（git 可 review / diff / 回滚），改提示词改 .md 即可、重启生效，无需动代码。
`Settings.chat_system_prompt_path` 仅作可选 override（默认空 → 用本模块内置的 system.md）。
"""

from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings

_DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"


@lru_cache(maxsize=1)
def get_system_prompt() -> str:
    """读取对话系统提示词。优先用 Settings 的 override 路径，否则用模块内置 system.md。"""
    override = get_settings().chat_system_prompt_path.strip()
    path = Path(override) if override else _DEFAULT_PROMPT_PATH
    return path.read_text(encoding="utf-8").strip()


# 向后兼容：保留模块级常量，agent.py 等导入方无需改动。
SYSTEM_PROMPT = get_system_prompt()
