import base64
import time

from openai import AsyncOpenAI

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.vlm.parser import parse_notsure_segments
from app.core.vlm.schemas import VLMResponse

logger = get_logger("vlm.client")

_vlm_client: "VLMClient | None" = None


def get_vlm_client() -> "VLMClient":
    """FastAPI 依赖注入入口。业务模块统一通过 Depends(get_vlm_client) 获取。

    Example:
        @router.post("/chat")
        async def chat(vlm: VLMClient = Depends(get_vlm_client)):
            response = await vlm.extract_page(...)
    """
    global _vlm_client
    if _vlm_client is None:
        _vlm_client = VLMClient(get_settings())
    return _vlm_client


class VLMClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key.get_secret_value(),
        )
        self.default_model = settings.litellm_default_vlm_model

    async def extract_page(
        self,
        image_bytes: bytes,
        prompt: str,
        model: str | None = None,
    ) -> VLMResponse:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        target_model = model or self.default_model

        t0 = time.perf_counter()
        response = await self.client.chat.completions.create(
            model=target_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        raw_text = response.choices[0].message.content or ""
        usage = response.usage

        logger.info(
            "vlm_extract_page",
            model=target_model,
            latency_ms=latency_ms,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            notsure_count=raw_text.count("<notsure>"),
        )

        return VLMResponse(
            raw_text=raw_text,
            notsure_segments=parse_notsure_segments(raw_text),
            model_id=response.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            latency_ms=latency_ms,
        )
