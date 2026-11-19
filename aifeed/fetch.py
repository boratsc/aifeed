"""Helpers for parsing public news feeds."""
from urllib.parse import urlsplit

import feedparser
from bs4 import BeautifulSoup


def parse_feed(data: bytes) -> list[dict]:
    parsed = feedparser.parse(data)
    items = []
    for entry in parsed.entries:
        url = entry.get("link", "")
        parsed_url = urlsplit(url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            continue
        contents = entry.get("content", [])
        html = contents[0].get("value", "") if contents else entry.get("summary", "")
        items.append({
            "url": url,
            "title": entry.get("title", ""),
            "content": BeautifulSoup(html, "html.parser").get_text(" ", strip=True),
        })
    return items
