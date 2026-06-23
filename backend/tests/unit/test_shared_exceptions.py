"""验证 shared/ 包能正常 import + 业务模块可以用 app.shared.exceptions 抛错。"""

import pytest

from app.shared.exceptions import (
    BaseAppException,
    ConflictError,
    NotFoundError,
    UnsupportedFileTypeError,
    ValidationError,
)


def test_not_found_error_attributes() -> None:
    e = NotFoundError("user 123 not found")
    assert e.http_status == 404
    assert e.code == "not_found"
    assert str(e) == "user 123 not found"
    assert isinstance(e, BaseAppException)


def test_validation_error_inheritance() -> None:
    e = UnsupportedFileTypeError("xls 不支持")
    assert e.http_status == 400
    assert e.code == "unsupported_file_type"
    assert isinstance(e, ValidationError)
    assert isinstance(e, BaseAppException)


def test_can_override_code() -> None:
    e = ConflictError("dup", code="duplicate_sha256")
    assert e.code == "duplicate_sha256"
    assert e.http_status == 409


def test_can_raise_and_catch() -> None:
    with pytest.raises(BaseAppException) as exc_info:
        raise NotFoundError("test")
    assert exc_info.value.http_status == 404
