"""OpenAI-compatible chat client for the LLM gateway, with JSON validation + retry."""
import json
import random
import re
import threading
import time

import httpx

from .config import Settings

# Polish typography rule: no em dashes. URLs are protected before replacement.
_URL_RE = re.compile(r"https?://\S+")

# Który model faktycznie odpowiedział (gateway fill-first może podmienić).
# Thread-local: chat jest współdzielony przez 4 workery edytora.
_state = threading.local()


def last_model() -> str | None:
    return getattr(_state, "model", None)


class LLMError(Exception):
    pass


def clean_pl(text: str) -> str:
    urls: list[str] = []

    def stash(m):
        urls.append(m.group(0))
        return f"\x00{len(urls) - 1}\x00"

    text = _URL_RE.sub(stash, text)
    text = text.replace(" — ", ", ").replace("—", ",")
    text = re.sub(r"[ \t]+", " ", text)
    for i, u in enumerate(urls):
        text = text.replace(f"\x00{i}\x00", u)
    return text


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def chat(settings: Settings, model: str, system: str, user: str,
         json_mode: bool = True, max_retries: int = 2) -> dict | str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_err = None
    # reasoning models spend tokens on thinking before content: generous budget
    # Streaming keeps long NIM generations alive (non-stream connections get EOF
    # after ~340s of silent reasoning) and lets reasoning models budget tokens.
    payload = {"model": model, "messages": messages, "max_tokens": 16000, "stream": True}
    with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0)) as client:
        for attempt in range(max_retries + 1):
            parts: list[str] = []
            reasoning: list[str] = []
            # 429/503: back off and retry the SAME model. Relying on the gateway's
            # fill-first fallback here risks 400 "must be passed back" when a
            # reasoning request lands on a non-reasoning provider mid-flight.
            for http_attempt in range(settings.editor_429_retries + 1):
                try:
                    with client.stream(
                        "POST",
                        f"{settings.gateway_url}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {settings.gateway_key}"},
                    ) as resp:
                        resp.raise_for_status()
                        for line in resp.iter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except (json.JSONDecodeError, ValueError):
                                continue
                            model = chunk.get("model")
                            if model:
                                _state.model = model
                            for choice in chunk.get("choices", []):
                                delta = choice.get("delta", {})
                                if delta.get("content"):
                                    parts.append(delta["content"])
                                if delta.get("reasoning_content"):
                                    reasoning.append(delta["reasoning_content"])
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in (429, 503) or http_attempt == settings.editor_429_retries:
                        raise
                    time.sleep(2 ** (http_attempt + 1) + random.uniform(0, 1))
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                    # przejściowa awaria DNS/sieci nie może zerować biegu
                    if http_attempt == settings.editor_429_retries:
                        raise
                    time.sleep(2 ** (http_attempt + 1))
            # Reasoning models (NIM/Nemotron/Mistral-T) may return only reasoning_content.
            content = "".join(parts) or "".join(reasoning)
            if not json_mode:
                return content
            try:
                return json.loads(_strip_fence(content))
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e
                messages = messages[:2] + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": "Odpowiedź nie była poprawnym JSON. Zwróć WYŁĄCZNIE poprawny JSON, bez komentarzy i bez fence."},
                ]
                payload["messages"] = messages
    raise LLMError(f"no valid JSON after {max_retries + 1} attempts: {last_err}")