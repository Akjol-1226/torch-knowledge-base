"""项目通用异常基类。

业务模块禁止 raise raw Exception；统一使用本模块的子类。
新增业务异常应继承 BaseAppException（或合适的子类）。

异常 → HTTP status code 的映射由 app/main.py 的 exception handlers 处理。
"""


class BaseAppException(Exception):
    """所有业务异常的基类。"""

    http_status: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFoundError(BaseAppException):
    """资源不存在（404）。"""

    http_status = 404
    code = "not_found"


class ValidationError(BaseAppException):
    """业务校验失败（400）。"""

    http_status = 400
    code = "validation_error"


class UnauthorizedError(BaseAppException):
    """未授权（401）。"""

    http_status = 401
    code = "unauthorized"


class ForbiddenError(BaseAppException):
    """无权限（403）。"""

    http_status = 403
    code = "forbidden"


class ConflictError(BaseAppException):
    """资源冲突（409，如重复 sha256）。"""

    http_status = 409
    code = "conflict"


class UnsupportedFileTypeError(ValidationError):
    """文件类型不支持（入库模块预留）。"""

    code = "unsupported_file_type"
