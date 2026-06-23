"""验证 get_vlm_client 依赖注入 provider 正常工作。"""

import pytest

from app.core.vlm import client as vlm_client_mod
from app.core.vlm.client import VLMClient, get_vlm_client


def test_get_vlm_client_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://test.local")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_DEFAULT_VLM_MODEL", "test-model")

    from app.core.config import get_settings

    # reset module-level cache
    vlm_client_mod._vlm_client = None
    get_settings.cache_clear()

    c1 = get_vlm_client()
    c2 = get_vlm_client()

    assert isinstance(c1, VLMClient)
    assert c1 is c2  # 单例

    # cleanup
    vlm_client_mod._vlm_client = None
