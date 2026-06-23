"""手动 smoke test：验证 LiteLLM 真实连通。

Run: uv run python -m app.core.vlm.smoke_test
需要 .env 配齐 LITELLM_* 三项。
"""

import asyncio
import base64
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.vlm.client import VLMClient

logger = get_logger("vlm.smoke")


async def main() -> int:
    settings = get_settings()
    configure_logging(settings)

    img_path = Path(__file__).parent / "fixtures" / "smoke.png"
    if not img_path.exists():
        # 1x1 透明 PNG 占位
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )
        img_path.parent.mkdir(exist_ok=True)
        img_path.write_bytes(png_bytes)

    client = VLMClient(settings)
    resp = await client.extract_page(
        image_bytes=img_path.read_bytes(),
        prompt="描述这张图。如果有不确定的部分，用 <notsure>...</notsure> 标记。",
    )

    logger.info(
        "smoke_test_ok",
        model=resp.model_id,
        latency_ms=resp.latency_ms,
        tokens=resp.prompt_tokens + resp.completion_tokens,
        raw_text_preview=resp.raw_text[:200],
    )
    print("\n=== Smoke Test OK ===")
    print(f"Model: {resp.model_id}")
    print(f"Latency: {resp.latency_ms}ms")
    print(f"Raw text:\n{resp.raw_text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
