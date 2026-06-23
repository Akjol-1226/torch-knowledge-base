import os
import threading
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    api_key: str
    api_base: str
    model: str
    outline_model: str
    relevel_model: str
    relevel_max_tokens: int
    relevel_timeout: float
    enable_relevel: bool
    enable_thinking: bool
    temperature: float
    top_p: float
    max_tokens: int
    seed: int | None
    pdf_render_dpi: int
    max_previous_tail_chars: int
    pymupdf_text_min_chars: int
    pymupdf_structure_confidence_min: float
    phase2_max_workers: int
    repair_tail_continuations: bool


def _optional_int_env(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return int(raw)


_config: Config | None = None
_config_lock = threading.Lock()


def get_config() -> Config:
    global _config
    if _config is not None:
        return _config
    with _config_lock:
        if _config is None:
            model = os.getenv("QWEN_MODEL", "qwen3.6-plus")
            outline_model = os.getenv("QWEN_OUTLINE_MODEL", "").strip() or model
            _config = Config(
                api_key=os.environ.get("QWEN_API_KEY", ""),
                api_base=os.getenv("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                model=model,
                outline_model=outline_model,
                relevel_model=os.getenv("QWEN_RELEVEL_MODEL", "qwen3.7-max").strip(),
                relevel_max_tokens=int(os.getenv("QWEN_RELEVEL_MAX_TOKENS", "32768")),
                relevel_timeout=float(os.getenv("QWEN_RELEVEL_TIMEOUT", "600")),
                enable_relevel=os.getenv("ENABLE_RELEVEL", "true").lower() == "true",
                enable_thinking=os.getenv("QWEN_ENABLE_THINKING", "false").lower() == "true",
                temperature=float(os.getenv("QWEN_TEMPERATURE", "0")),
                top_p=float(os.getenv("QWEN_TOP_P", "0.1")),
                max_tokens=int(os.getenv("QWEN_MAX_TOKENS", "8192")),
                seed=_optional_int_env("QWEN_SEED"),
                pdf_render_dpi=int(os.getenv("PDF_RENDER_DPI", "600")),
                max_previous_tail_chars=int(os.getenv("MAX_PREVIOUS_TAIL_CHARS", "300")),
                pymupdf_text_min_chars=int(os.getenv("PYMUPDF_TEXT_MIN_CHARS", "50")),
                pymupdf_structure_confidence_min=float(os.getenv("PYMUPDF_STRUCTURE_CONFIDENCE_MIN", "0.45")),
                phase2_max_workers=int(os.getenv("PHASE2_MAX_WORKERS", "16")),
                repair_tail_continuations=os.getenv("REPAIR_TAIL_CONTINUATIONS", "false").lower() == "true",
            )
    return _config
