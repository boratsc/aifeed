import json

import httpx
import pytest

from aifeed.config import Settings
from aifeed.llm import LLMError, chat, clean_pl
from aifeed.embed import vector


_RealClient = httpx.Client


def make_settings():
    return Settings(gateway_url="https://gw.test/v1", gateway_key="k", embed_url="http://embed.test")


def mock_transport(responses):
    """responses: list of content strings, returned in order per call."""
    calls = {"n": 0}

    def handler(request):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"content": responses[i]}}],
            "model": "test",
        })

    return httpx.MockTransport(handler), calls


def test_chat_verifies_tls_by_default(monkeypatch):
    transport, _ = mock_transport(['{"ok": true}'])
    options = {}

    def client(**kwargs):
        options.update(kwargs)
        return _RealClient(transport=transport)

    monkeypatch.setattr(httpx, "Client", client)
    assert chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert options.get("verify", True) is True


def test_chat_parses_clean_json(monkeypatch):
    transport, _ = mock_transport(['{"a": 1}'])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    result = chat(make_settings(), "m", "sys", "usr")
    assert result == {"a": 1}


def test_chat_strips_json_fence(monkeypatch):
    transport, _ = mock_transport(['```json\n{"a": 2}\n```'])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    assert chat(make_settings(), "m", "sys", "usr") == {"a": 2}


def test_chat_retries_then_raises(monkeypatch):
    transport, calls = mock_transport(["not json", "still not json", "garbage"])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    with pytest.raises(LLMError):
        chat(make_settings(), "m", "sys", "usr", max_retries=2)
    assert calls["n"] == 3  # initial + 2 retries


def test_chat_recovers_on_retry(monkeypatch):
    transport, calls = mock_transport(["oops", '{"ok": true}'])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    assert chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert calls["n"] == 2


def test_chat_text_mode(monkeypatch):
    transport, _ = mock_transport(["plain text"])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    assert chat(make_settings(), "m", "sys", "usr", json_mode=False) == "plain text"


def test_clean_pl_removes_em_dash():
    assert clean_pl("Model — szybki i tani") == "Model, szybki i tani"
    assert clean_pl("coś—innego") == "coś,innego"


def test_clean_pl_keeps_urls_intact():
    text = "Link: https://x.com/a—b oraz opis — z myślnikiem"
    out = clean_pl(text)
    assert "https://x.com/a—b" in out
    assert "opis, z myślnikiem" in out


def test_embed_returns_vector(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "qwen3-embedding:0.6b"
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    assert vector(make_settings(), "text") == [0.1, 0.2, 0.3]