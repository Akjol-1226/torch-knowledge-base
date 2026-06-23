import pytest

from app.core.config import Settings


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://test.local")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_DEFAULT_VLM_MODEL", "test-model")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.litellm_base_url == "http://test.local"
    assert s.litellm_api_key.get_secret_value() == "sk-test"
    assert s.litellm_default_vlm_model == "test-model"
    assert s.app_env == "development"  # 默认值


def test_settings_log_level_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://x")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-x")
    monkeypatch.setenv("LITELLM_DEFAULT_VLM_MODEL", "m")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.log_level == "INFO"
