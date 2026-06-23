import os
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    # LiteLLM Proxy（OpenAI 兼容）——三条 LLM 路径（PDF 解析 / 建树 / 对话）统一入口。
    # 默认空：没配 .env 时 server 仍能起、测试仍能跑，仅真实 LLM 调用需要凭证。
    litellm_base_url: str = ""
    litellm_api_key: SecretStr = SecretStr("")
    litellm_default_vlm_model: str = ""

    # 知识库引擎（搬自 pageindex-agent）
    data_dir: Path = _BACKEND_ROOT / "data"  # 文档树 / BM25 索引 / 目录的文件存储根
    index_model: str = ""  # 建树模型，litellm 路由名，如 "openai/gpt-4o"
    chat_model: str = ""  # 对话模型，如 "openai/gpt-4o" 或 proxy 上的模型名

    database_url: str = "sqlite+aiosqlite:///./torch_kb.db"

    # 对话系统提示词的路径 override（默认空 → 用 modules/chat/prompts/system.md）。
    # 提示词正文是版本化资产、放仓库文件里（改 .md 即可，重启生效）；这里只存可选指针，
    # 用于切换/灰度不同提示词，不要把大段正文塞进 env。
    chat_system_prompt_path: str = ""

    def apply_litellm_env(self) -> None:
        """把 LiteLLM Proxy 凭证写进 OPENAI_* env。

        pageindex 建树内部用 litellm 库直连、纯靠 env 读凭证（见 core/pageindex/utils.py），
        故建树前需调用本方法，把统一的 litellm_* 配置桥接到 litellm 期望的 OPENAI_* 变量。
        """
        if self.litellm_base_url:
            os.environ["OPENAI_BASE_URL"] = self.litellm_base_url
            os.environ["OPENAI_API_BASE"] = self.litellm_base_url
        key = self.litellm_api_key.get_secret_value()
        if key:
            os.environ["OPENAI_API_KEY"] = key

    def apply_docparse_env(self) -> None:
        """把 LiteLLM Proxy 凭证 + vision 模型桥接到 DocVisionMD 期望的 QWEN_* env。

        DocVisionMD（core/docparse）内部读 QWEN_*（见 core/docparse/config.py，单例）。
        PDF→md 是 VLM 任务，统一用 litellm_default_vlm_model 走同一 Proxy。
        必须在首次调用 convert_pdf_to_markdown（docparse get_config 初始化）前调用。
        """
        vlm = self.litellm_default_vlm_model or "gpt-4o"
        if self.litellm_base_url:
            os.environ["QWEN_API_BASE"] = self.litellm_base_url
        key = self.litellm_api_key.get_secret_value()
        if key:
            os.environ["QWEN_API_KEY"] = key
        os.environ["QWEN_MODEL"] = vlm
        os.environ["QWEN_OUTLINE_MODEL"] = vlm
        os.environ["QWEN_RELEVEL_MODEL"] = vlm
        os.environ.setdefault("QWEN_RELEVEL_MAX_TOKENS", "8192")
        # 关闭 qwen3 的 thinking（默认就关，显式声明）：qwen_client 据此传
        # extra_body={"enable_thinking": False}
        os.environ.setdefault("QWEN_ENABLE_THINKING", "false")
        # DocVisionMD 默认 600DPI 是为 qwen-vl-max（吃高清大图）；gpt-4o vision 对超大图会
        # hang/超时（内部本就 downscale），降到 200DPI 既够清晰又能正常响应。可用 .env 覆盖。
        os.environ.setdefault("PDF_RENDER_DPI", "200")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
