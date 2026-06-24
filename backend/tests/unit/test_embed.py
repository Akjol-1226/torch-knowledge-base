from types import SimpleNamespace

from pydantic import SecretStr

from app.core.retrieval.embed import ProxyEmbeddingClient, _build_client, compose_text


def _settings(**kw):
    base = dict(
        embedding_provider="proxy",
        litellm_base_url="http://gw.local",
        litellm_api_key=SecretStr("k"),
        embedding_model="text-embedding-v4",
        embedding_batch_size=10,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_compose_text_order_summary_before_text():
    rec = {"title": "T", "summary": "S", "text": "X"}
    assert compose_text(rec, 100) == "T\nS\nX"


def test_compose_text_truncates():
    rec = {"title": "a" * 50, "summary": "", "text": "b" * 50}
    assert len(compose_text(rec, 30)) == 30


def test_compose_text_skips_empty_parts():
    rec = {"title": "T", "summary": "", "text": "X"}
    assert compose_text(rec, 100) == "T\nX"


def test_build_client_proxy_ok():
    c = _build_client(_settings())
    assert isinstance(c, ProxyEmbeddingClient)
    assert c.signature == "proxy|text-embedding-v4"


def test_build_client_none_when_model_missing():
    assert _build_client(_settings(embedding_model="")) is None


def test_build_client_none_when_no_creds():
    assert _build_client(_settings(litellm_base_url="")) is None


def test_build_client_local_unsupported_returns_none():
    assert _build_client(_settings(embedding_provider="local")) is None
