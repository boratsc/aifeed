"""Helpers for parsing public news feeds."""
from calendar import timegm
from datetime import datetime, timezone
import re
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup


class FetchError(Exception):
    """A source could not be interpreted as a feed."""


def ingest_feeds(client: httpx.Client, store, sources: dict[str, str], since_ts: float) -> tuple[int, dict[str, str]]:
    items, errors = fetch_sources(client, sources)
    inserted = 0
    for item in items:
        published_at = item["published_at"]
        if published_at is None or published_at < since_ts:
            continue
        before = store.db.total_changes
        store.upsert_raw(item["source_id"], item["url"], item["title"], published_at, item["content"], item.get("og_image"))
        inserted += store.db.total_changes - before
    return inserted, errors


def fetch_sources(client: httpx.Client, sources: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    items, errors = [], {}
    for source_id, url in sources.items():
        try:
            if source_id in {"cursor_changelog", "deepseek_updates", "hf_qwen", "hf_zhipu"}:
                try:
                    response = client.get(url, timeout=20, follow_redirects=True)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise FetchError(f"failed to fetch changelog: {exc}") from exc
                if source_id.startswith("hf_"):
                    try:
                        models = response.json()
                        if not isinstance(models, list):
                            raise ValueError("model list expected")
                        entries = parse_hf_models(models)
                    except ValueError as exc:
                        raise FetchError("invalid model API response") from exc
                else:
                    parser = parse_cursor if source_id == "cursor_changelog" else parse_deepseek
                    entries = parser(response.content)
            else:
                entries = fetch_feed(client, url)
            items.extend({**item, "source_id": source_id} for item in entries)
        except FetchError as exc:
            errors[source_id] = str(exc)
    return items, errors


def fetch_feed(client: httpx.Client, url: str) -> list[dict]:
    try:
        response = client.get(url, timeout=20, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchError(f"failed to fetch feed: {exc}") from exc
    return parse_feed(response.content)


def parse_hf_models(models: list[dict]) -> list[dict]:
    items = []
    for model in models:
        if not isinstance(model, dict):
            continue
        model_id = model.get("id", "")
        if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", model_id):
            continue
        try:
            date = datetime.fromisoformat(model["createdAt"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                continue
        except (KeyError, ValueError, AttributeError):
            continue
        items.append({"url": "https://huggingface.co/" + model_id, "title": model_id,
                      "published_at": date.timestamp(), "content": ""})
    return items


def parse_deepseek(data: bytes) -> list[dict]:
    soup = BeautifulSoup(data, "html.parser")
    items = []
    for heading in soup.select("main article h2[id]"):
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", heading.get_text(" ", strip=True))
        if not match:
            continue
        try:
            published = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        parts = []
        title = None
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) == "h2":
                break
            if getattr(sibling, "name", None):
                text = sibling.get_text(" ", strip=True).replace("​", "").strip()
                if sibling.name == "h3" and title is None:
                    title = text
                if text:
                    parts.append(text)
        if title:
            items.append({"url": "https://api-docs.deepseek.com/updates#" + heading["id"],
                          "title": title, "published_at": published, "content": " ".join(parts)})
    if not items:
        raise FetchError("no dated DeepSeek changelog entries found")
    return items


def parse_cursor(data: bytes) -> list[dict]:
    items = []
    for article in BeautifulSoup(data, "html.parser").select("article"):
        date = article.select_one("time[datetime]")
        title = article.select_one('h1 a[href^="/changelog/"], h2 a[href^="/changelog/"]')
        if not date or not title:
            continue
        try:
            published = datetime.fromisoformat(date["datetime"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        items.append({"url": urljoin("https://cursor.com", title["href"]),
                      "title": title.get_text(" ", strip=True),
                      "published_at": published,
                      "content": article.get_text(" ", strip=True)})
    if not items:
        raise FetchError("no dated Cursor changelog entries found")
    return items


def _extract_image(entry) -> str | None:
    media = entry.get("media_thumbnail") or entry.get("media_content")
    if isinstance(media, list) and media:
        url = media[0].get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    enclosure = entry.get("enclosures")
    if enclosure and enclosure[0].get("type", "").startswith("image/"):
        url = enclosure[0].get("href")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return None


def parse_feed(data: bytes) -> list[dict]:
    parsed = feedparser.parse(data)
    if not parsed.version:
        raise FetchError("response is not a recognized RSS or Atom feed")
    items = []
    for entry in parsed.entries:
        url = entry.get("link", "")
        parsed_url = urlsplit(url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            continue
        contents = entry.get("content", [])
        html = contents[0].get("value", "") if contents else entry.get("summary", "")
        og_image = _extract_image(entry)
        items.append({
            "url": url,
            "title": entry.get("title", ""),
            "published_at": timegm(entry.published_parsed) if entry.get("published_parsed") else None,
            "content": BeautifulSoup(html, "html.parser").get_text(" ", strip=True),
            "og_image": og_image,
        })
    return items
