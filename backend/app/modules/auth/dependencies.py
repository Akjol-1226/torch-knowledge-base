"""权限依赖注入框架占位。

v0 骨架阶段：返回一个固定的 anonymous user，方便其他模块直接 Depends(current_user)
而不被未实现的鉴权挡住。
真实 RBAC 实现等 PRD 权限模块 task。
"""

from pydantic import BaseModel


class CurrentUser(BaseModel):
    id: int = 0
    username: str = "anonymous"
    is_admin: bool = False


async def get_current_user() -> CurrentUser:
    return CurrentUser()
