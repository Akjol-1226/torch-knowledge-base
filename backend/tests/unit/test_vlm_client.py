from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.vlm.client import VLMClient


@pytest.fixture
def fake_settings() -> MagicMock:
    s = MagicMock()
    s.litellm_base_url = "http://fake"
    s.litellm_api_key.get_secret_value.return_value = "sk-fake"
    s.litellm_default_vlm_model = "fake-vlm"
    return s


async def test_extract_page_returns_vlm_response(fake_settings: MagicMock) -> None:
    client = VLMClient(fake_settings)

    fake_message = MagicMock()
    fake_message.content = "extracted text with <notsure>doubt</notsure>"
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_usage = MagicMock()
    fake_usage.prompt_tokens = 10
    fake_usage.completion_tokens = 20
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage = fake_usage
    fake_response.model = "fake-vlm"

    client.client.chat.completions.create = AsyncMock(return_value=fake_response)

    result = await client.extract_page(
        image_bytes=b"\x89PNG fake",
        prompt="describe page",
    )

    assert result.raw_text == "extracted text with <notsure>doubt</notsure>"
    assert len(result.notsure_segments) == 1
    assert result.notsure_segments[0].text == "doubt"
    assert result.model_id == "fake-vlm"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 20


async def test_extract_page_uses_custom_model(fake_settings: MagicMock) -> None:
    client = VLMClient(fake_settings)

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="x"))]
    fake_response.usage = MagicMock(prompt_tokens=0, completion_tokens=0)
    fake_response.model = "custom"

    client.client.chat.completions.create = AsyncMock(return_value=fake_response)

    await client.extract_page(image_bytes=b"x", prompt="y", model="override-model")

    call_kwargs = client.client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "override-model"
