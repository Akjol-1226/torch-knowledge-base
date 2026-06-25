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

    max_upload_mb: int = 100  # 单文件上传上限（MB）；超过返回 413，防大文件 OOM

    # 多格式上传：非 PDF（docx/xlsx/pptx/txt 等）先经 Gotenberg 转 PDF，再走现有 PDF 管线。
    # Gotenberg 是独立容器（封装 LibreOffice），见 docker-compose + docker/gotenberg。
    gotenberg_url: str = "http://localhost:3000"
    gotenberg_timeout: int = 180  # 转换超时（秒）；大文档 LibreOffice 渲染较慢

    pdf_render_dpi: int = 500  # PDF→PNG 渲染 DPI（喂 VLM 解析）；.env 的 PDF_RENDER_DPI 覆盖
    ocr_render_dpi: int = 200  # OCR 侧车渲染 DPI（只画高亮框、不需高清）；与 VLM DPI 解耦，GPU 上快
    ocr_use_gpu: bool = True  # OCR 默认 GPU(CUDA)；OCR_USE_GPU=false 强制 CPU（无 GPU 也自动回退）

    # —— 混合检索（BM25 + 向量，RRF 融合）。见 docs/plans/2026-06-24-hybrid-retrieval-design.md ——
    # 向量是增强项、BM25 是底线：embedding 不可用 / 索引缺失 / hybrid off → 自动退回纯 BM25。
    hybrid_enabled: bool = True
    # provider: "proxy"（走 LiteLLM Proxy /embeddings）| "local"（预留 onnx 兜底）
    embedding_provider: str = "proxy"
    embedding_model: str = "text-embedding-v4"  # provider=proxy 时的模型路由名（Proxy 上的 id）
    embedding_max_chars: int = 6000  # 单节点编码输入截断（字符近似，给 ~8K token 留余量）
    embedding_batch_size: int = 10  # 每次 /embeddings 请求条数（Qwen embedding 家族保守取 10）
    retrieval_top_n: int = 50  # 向量召回候选数
    rrf_k: int = 60  # RRF 平滑常数
    rrf_w_bm25: float = 0.6  # BM25 一路权重（主）
    rrf_w_vec: float = 0.4  # 向量一路权重（辅）
    # 向量准入阈值：低于此的命中不参与融合。取低值——RRF 已让弱向量"沉默而非拖累"，靠融合抑噪
    vec_sim_threshold: float = 0.15

    # —— 建树（PageIndex）——
    # 子树合并阈值：整棵子树文本 token 数低于此的父节点，把子节点正文并入自身，
    # 避免 VLM 产出的大量空壳标题各自成节点、稀释向量并占满候选名额。0 关闭瘦身。
    tree_thinning_min_tokens: int = 300

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
        # 渲染 DPI 由 config 统一管（pdf_render_dpi，默认 500，.env 可覆盖）。
        # VLM 现为 qwen 系视觉模型（吃高清大图），用较高 DPI 提升复杂表格/小字解析质量。
        os.environ["PDF_RENDER_DPI"] = str(self.pdf_render_dpi)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
