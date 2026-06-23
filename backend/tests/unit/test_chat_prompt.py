import types

from app.modules.chat import prompt as prompt_mod


def test_system_prompt_loaded_from_file():
    p = prompt_mod._DEFAULT_PROMPT_PATH
    assert p.exists() and p.name == "system.md"
    text = prompt_mod.get_system_prompt()
    assert text == p.read_text(encoding="utf-8").strip()
    assert text  # 非空
    assert "[[cite:" in text  # torch 句末引用范式仍在


def test_module_constant_matches_loader():
    assert prompt_mod.SYSTEM_PROMPT == prompt_mod.get_system_prompt()


def test_settings_path_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom.md"
    custom.write_text("自定义提示词", encoding="utf-8")
    monkeypatch.setattr(
        prompt_mod, "get_settings",
        lambda: types.SimpleNamespace(chat_system_prompt_path=str(custom)),
    )
    prompt_mod.get_system_prompt.cache_clear()
    try:
        assert prompt_mod.get_system_prompt() == "自定义提示词"
    finally:
        prompt_mod.get_system_prompt.cache_clear()  # 还原，避免污染其它测试
