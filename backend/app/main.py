from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

_DESIGN_DIR = Path(__file__).resolve().parents[2] / "product" / "design"
_MOCKUP = _DESIGN_DIR / "v0-mockup-v2.html"
_VENDOR_DIR = _DESIGN_DIR / "vendor"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    logger = get_logger("app.lifespan")
    from app.core.db import init_db

    await init_db()
    logger.info("app_starting", app_env=settings.app_env)
    yield
    logger.info("app_stopped")


app = FastAPI(
    title="火炬电子知识库 v0",
    version="0.1.0",
    lifespan=lifespan,
)

# dev：允许前端设计稿（file:// 或任意端口）直接连本服务。生产应收紧 allow_origins。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    """dev：直接 serve 前端设计稿，浏览器开 http://localhost:8000/ 即同源使用（无 CORS 顾虑）。"""
    # 禁缓存：dev 期改了前端不用清浏览器缓存（避免"看不到改动"）
    return FileResponse(_MOCKUP, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/vendor/{name}")
def vendor(name: str) -> FileResponse:
    """本地内置前端第三方库（如 mermaid.min.js），避免依赖外网 CDN（国内网络下更可靠）。"""
    p = (_VENDOR_DIR / name).resolve()
    # 防目录穿越：只允许 vendor 目录下的真实文件
    if _VENDOR_DIR not in p.parents or not p.is_file():
        raise HTTPException(status_code=404, detail=f"vendor asset not found: {name}")
    # 第三方库带 hash 不会变，可长缓存
    return FileResponse(p, headers={"Cache-Control": "public, max-age=31536000, immutable"})


from app.modules.chat.router import router as chat_router  # noqa: E402
from app.modules.ingest.router import router as ingest_router  # noqa: E402

app.include_router(ingest_router)
app.include_router(chat_router)
