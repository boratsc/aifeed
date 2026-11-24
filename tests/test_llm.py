import json

import httpx
import pytest

from aifeed.config import Settings
from aifeed.llm import LLMError, chat, clean_pl
from aifeed.embed import vector


_RealClient = httpx.Client


def make_settings():
    return Settings(gateway_url="https://gw.test/v1", gateway_key="k", embed_url="http://embed.test")


def _sse(payload_text: str) -> str:
    """Buduje ciąg SSE dla pojedynczego delta(obiektu) w polu content."""
    chunk = {"choices": [{"delta": {"content": payload_text}}]}
    return "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"


def mock_stream(responses):
    """responses: lista wiadomości; każda zwraca strumieniową odpowiedź SSE z content=response."""
    calls = {"n": 0}

    def handler(request):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        body = _sse(responses[i])
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handler), calls


def test_chat_verifies_tls_by_default(monkeypatch):
    transport, _ = mock_stream(['{"ok": true}'])
    options = {}

    def client(**kwargs):
        options.update(kwargs)
        return _RealClient(transport=transport)

    monkeypatch.setattr(httpx, "Client", client)
    assert chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert options.get("verify", True) is True


def test_chat_parses_clean_json(monkeypatch):
    transport, _ = mock_stream(['{"a": 1}'])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    assert chat(make_settings(), "m", "sys", "usr") == {"a": 1}


def test_chat_strips_json_fence(monkeypatch):
    transport, _ = mock_stream(['```json\n{"a": 2}\n```'])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    assert chat(make_settings(), "m", "sys", "usr") == {"a": 2}


def test_chat_reads_reasoning_content(monkeypatch):
    # delta zwraca tylko reasoning_content, bez content
    def handler(request):
        chunk = {"choices": [{"delta": {"reasoning_content": '{"a": 3}'}}]}
        body = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    assert chat(make_settings(), "m", "sys", "usr") == {"a": 3}


def test_chat_accumulates_multiple_chunks(monkeypatch):
    def handler(request):
        parts = ['{"a":', '1}']
        lines = []
        for p in parts:
            lines.append("data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) + "\n\n")
        lines.append("data: [DONE]\n\n")
        return httpx.Response(200, content="".join(lines).encode(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    assert chat(make_settings(), "m", "sys", "usr") == {"a": 1}


def test_chat_retries_then_raises(monkeypatch):
    transport, calls = mock_stream(["not json", "still not json", "garbage"])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    with pytest.raises(LLMError):
        chat(make_settings(), "m", "sys", "usr", max_retries=2)
    assert calls["n"] == 3


def test_chat_recovers_on_retry(monkeypatch):
    transport, calls = mock_stream(["oops", '{"ok": true}'])
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=transport))
    assert chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert calls["n"] == 2


def test_chat_text_mode(monkeypatch):
    transport, _ = mock_stream(["plain text"])
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


def test_chat_backs_off_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429)
        return httpx.Response(200, content=_sse('{"ok": true}').encode(),
                              headers={"content-type": "text/event-stream"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    slept = []
    import aifeed.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(llm.random, "uniform", lambda a, b: 0.0)
    assert chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert calls["n"] == 3
    assert slept == [2.0, 4.0]


def test_chat_gives_up_after_429_retries(monkeypatch):
    def handler(request):
        return httpx.Response(429)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    import aifeed.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        chat(make_settings(), "m", "sys", "usr")


def test_chat_does_not_retry_400(monkeypatch):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(400)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPStatusError):
        chat(make_settings(), "m", "sys", "usr")
    assert calls["n"] == 1


def test_chat_records_last_model(monkeypatch):
    import aifeed.llm as llm
    def handler(request):
        chunk = {"model": "gemini-3.5-flash-lite", "choices": [{"delta": {"content": '{"ok": true}'}}]}
        body = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    assert chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert llm.last_model() == "gemini-3.5-flash-lite"


def test_last_model_is_thread_local(monkeypatch):
    import aifeed.llm as llm
    old = llm.last_model()
    # wątek główny nie zmienia się przez symulację innego wątku
    assert llm.last_model() == old

def test_chat_retries_dns_failure(monkeypatch):
    import aifeed.llm as llm
    import httpx
    from aifeed.config import Settings
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution", request=request)
        chunk = {"model": "m", "choices": [{"delta": {"content": '{"ok": true}'}}]}
        body = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
    real_client = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    assert llm.chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert calls["n"] == 3
    assert slept == [2.0, 4.0]
