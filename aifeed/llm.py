"""OpenAI-compatible chat client for the LLM gateway, with JSON validation + retry."""
import json
import re

import httpx

from .config import Settings

# Polish typography rule: no em dashes. URLs are protected before replacement.
_URL_RE = re.compile(r"https?://\S+")


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
    payload = {"model": model, "messages": messages, "max_tokens": 8000}
    with httpx.Client(timeout=180) as client:
        for attempt in range(max_retries + 1):
            resp = client.post(
                f"{settings.gateway_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.gateway_key}"},
            )
            resp.raise_for_status()
            message = resp.json()["choices"][0]["message"]
            # Reasoning models (NIM/Nemotron/Mistral-T) return reasoning_content instead of content.
            content = message.get("content") or message.get("reasoning_content") or ""
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