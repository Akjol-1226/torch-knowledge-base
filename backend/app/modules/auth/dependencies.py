"""权限依赖注入框架占位。

v0 阶段尚未接正式 RBAC：返回配置里的开发占位用户，方便其他模块直接 Depends(current_user)
而不被未实现的鉴权挡住。部署只读演示时可用 DEV_AUTH_IS_ADMIN=false 覆盖。
真实 RBAC 实现等 PRD 权限模块 task。
"""

from pydantic import BaseModel

from app.core.config import get_settings


class CurrentUser(BaseModel):
    id: int = 0
    username: str = "anonymous"
    is_admin: bool = False


async def get_current_user() -> CurrentUser:
    settings = get_settings()
    return CurrentUser(
        id=settings.dev_auth_user_id,
        username=settings.dev_auth_username,
        is_admin=settings.dev_auth_is_admin,
    )
