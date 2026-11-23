"""Helpers for parsing public news feeds."""
from calendar import timegm
from datetime import datetime, timezone
import re
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
import time
from bs4 import BeautifulSoup

from .sources import PROMO_SOURCES

# Część chińskich feedów (QbitAI) zwraca 403 bez nagłówka przeglądarki.
_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"


class FetchError(Exception):
    """A source could not be interpreted as a feed."""


def extract_article_text(data: bytes) -> str:
    """Best-effort full-text extraction: znane selektory, potem najdłuższy węzeł treściowy."""
    soup = BeautifulSoup(data, "html.parser")
    for selector in (".prose", "article", ".article", ".markdown-body", "main"):
        el = soup.select_one(selector)
        if el:
            return el.get_text(" ", strip=True)
    # Generic fallback: najdłuższy element treściowy (bez nawigacji/script/style).
    best = ""
    for el in soup.find_all(["div", "section", "article"]):
        if el.name in ("script", "style"):
            continue
        text = el.get_text(" ", strip=True)
        if len(text) > len(best):
            best = text
    return best


def extract_article_images(data: bytes, base_url: str | None = None) -> list[str]:
    """Figury/wykresy jako Markdown ![](url); pomija awatary i nie-bezpieczne URL."""
    soup = BeautifulSoup(data, "html.parser")
    out = []
    for selector in (".prose", "article", ".article", ".markdown-body", "main"):
        el = soup.select_one(selector)
        if el:
            break
    if el is None:
        return out
    for im in el.select("img"):
        src = (im.get("src") or "").strip()
        alt = (im.get("alt") or "").strip()
        if not src or not alt:
            continue
        low = src.lower()
        if "avatar" in low or "/avatars/" in low:
            continue
        if not src.startswith(("http://", "https://")):
            if base_url:
                src = urljoin(base_url, src)
            else:
                continue
        if not src.startswith(("http://", "https://")):
            continue
        out.append(f"![{alt}]({src})")
    return out


def _is_secondary_source(url: str) -> bool:
    return any(host in url for host in ('facebook.com', 'instagram.com', 'youtube.com', 'x.com', 'twitter.com')) or '/status/' in url


def firecrawl_search(settings, query: str, store=None) -> list[str]:
    if not settings.firecrawl_key or (store and store.firecrawl_used_this_month() >= settings.firecrawl_budget):
        return []
    try:
        with httpx.Client(timeout=45) as c:
            r = c.post("https://api.firecrawl.dev/v2/search", headers={"Authorization": f"Bearer {settings.firecrawl_key}"}, json={"query": query, "limit": 3})
        r.raise_for_status()
        if store:
            store.firecrawl_add_credit()
        return [x["url"] for x in r.json().get("data", {}).get("web", []) if x.get("url")]
    except (httpx.HTTPError, ValueError, AttributeError):
        return []


def sonar_search(settings, query: str, store=None) -> list[str]:
    if not settings.sonar_key or (store and store.sonar_used_cents_this_month() >= settings.sonar_budget_cents):
        return []
    try:
        with httpx.Client(timeout=45) as c:
            r = c.post("https://ai-gateway.vercel.sh/v1/chat/completions", headers={"Authorization": f"Bearer {settings.sonar_key}"}, json={"model": "perplexity/sonar", "messages": [{"role": "user", "content": "Find primary source URLs for this exact news. Output up to three direct URLs, no commentary. Do not guess URLs.\n" + query}], "max_tokens": 250})
        r.raise_for_status()
        d = r.json()
        if store:
            store.sonar_add_cost(float(d.get("usage", {}).get("cost", 0)))
        text = d["choices"][0]["message"]["content"]
        return re.findall(r"https?://[^\s<>\])]+", text)
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return []


def research_sources(settings, query: str, store=None) -> list[str]:
    """Firecrawl pierwszy, Sonar uzupełnia brakujące źródła."""
    urls = firecrawl_search(settings, query, store)
    if not urls or any(_is_secondary_source(u) for u in urls):
        urls += sonar_search(settings, query, store)
    return list(dict.fromkeys(urls))


_STOP = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "new", "not"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower()) if w not in _STOP}


_GENERIC = {"modelu", "modeli", "model", "models", "narzedzie", "narzedzia", "synchronizacja",
            "komentarzy", "priorytet", "kursorow", "wydanie", "wersji", "nowe", "danych"}


def is_relevant(article_title: str, text: str) -> bool:
    """Wymaga wspólnej nazwy własnej lub dwóch specyficznych słów; liczby i słowa ogólne nie liczą się."""
    title_tokens = _tokens(article_title)
    page_tokens = _tokens(text)
    def alpha(tokens):
        # "145b" to numer wersji, nie nazwa własna
        return {w for w in tokens if not w.isdigit() and not re.fullmatch(r"\d+\w?", w)}

    distinctive = alpha(title_tokens & page_tokens) - _GENERIC
    if len(distinctive) >= 2:
        return True
    # nazwa produktu zwykle stoi na początku tytułu ("Jync v2.4.5: ...")
    head = alpha({w for w in re.findall(r"[a-z0-9]{4,}", article_title.lower()[:40])})
    return bool(distinctive & head)


def research_materials(settings, store, article_id: int, limit=2) -> list[dict]:
    """Źródła uzupełniające jako materiały dla redaktora (flag research=True), nie dopisek do tekstu."""
    article = store.article_by_id(article_id)
    return research_for_topic(settings, store, article.title_pl, article.lead_pl,
                              {s.get('url') for s in article.sources}, limit)


def research_for_topic(settings, store, title, lead, known, limit=2):
    candidates = [u for u in research_sources(settings, f'{title}. {lead}', store)
                  if not _is_secondary_source(u) and u not in known]
    head = {w for w in re.findall(r"[a-z0-9]{4,}", title.lower()[:40]) if not w.isdigit()}
    candidates.sort(key=lambda u: 0 if any(h in u.lower() for h in head) else 1)
    out = []
    for url in candidates:
        if len(out) >= limit:
            break
        with httpx.Client() as c:
            try:
                resp = c.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": _UA})
                resp.raise_for_status()
                text = extract_article_text(resp.content)
            except httpx.HTTPError:
                continue
        if len(text) < 120 or not is_relevant(title, text):
            continue
        out.append({'id': None, 'source_id': 'research', 'url': url, 'title': f'Research: {url}',
                    'published_at': 0, 'content': text[:6000], 'research': True})
    return out


def enrich_article_with_research(settings, store, article_id: int, dry_run=False):
    """Uzupełnij artykuł treścią ze znalezionych, pasujących źródeł pierwotnych."""
    article = store.article_by_id(article_id)
    known = {source.get('url') for source in article.sources if isinstance(source, dict)}
    candidates = [u for u in research_sources(settings, f'{article.title_pl}. {article.lead_pl}', store)
                  if not _is_secondary_source(u)]
    # Najpierw domena pasująca do nazwy z tytułu (upstage.ai dla "Upstage...").
    head = {w for w in re.findall(r"[a-z0-9]{4,}", article.title_pl.lower()[:40]) if not w.isdigit()}
    candidates.sort(key=lambda u: 0 if any(h in u.lower() for h in head) else 1)
    for url in candidates:
        if url in known:
            continue
        with httpx.Client() as c:
            try:
                resp = c.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": _UA})
                resp.raise_for_status()
                text = extract_article_text(resp.content)
            except httpx.HTTPError:
                continue
        if len(text) < 120 or not is_relevant(article.title_pl, text):
            continue
        if dry_run:
            return {'url': url, 'chars': len(text)}
        store.update_article(article_id, body_pl=f'{article.body_pl}\n\n---\n\n{url}\n\n{text}')
        return True
    return False


def firecrawl_scrape(settings, url: str, store=None) -> str:
    """Ostatnia deska: render JS przez Firecrawl (SPA/blokady). Limit kredytów/mc z bazy."""
    if not settings.firecrawl_key:
        return ""
    if store is not None and store.firecrawl_used_this_month() >= settings.firecrawl_budget:
        return ""
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_key}"},
                json={"url": url, "formats": ["markdown"]},
            )
        resp.raise_for_status()
        data = resp.json()
        if store is not None:
            store.firecrawl_add_credit()
        if data.get("success"):
            return (data.get("data") or {}).get("markdown", "") or ""
    except (httpx.HTTPError, ValueError):
        pass
    return ""


def _oschina_api_text(client: httpx.Client, url: str) -> str:
    """oschina.net to SPA (pusty HTML); treść newsa jest w ich publicznym API."""
    m = re.match(r"https?://www\.oschina\.net/news/(\d+)", url)
    if not m:
        return ""
    try:
        resp = client.get(f"https://www.oschina.net/action/apiv2/news_detail?id={m.group(1)}",
                          timeout=15, headers={"User-Agent": _UA})
        resp.raise_for_status()
        body = resp.json().get("obj_data", {}).get("body", "")
        return BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
    except (httpx.HTTPError, ValueError, AttributeError):
        return ""


def ingest_feeds(client: httpx.Client, store, sources: dict[str, str], since_ts: float, settings=None) -> tuple[int, dict[str, str]]:
    items, errors = fetch_sources(client, sources)
    inserted = 0
    for item in items:
        published_at = item["published_at"]
        if published_at is None or published_at < since_ts:
            continue
        title = item.get("title") or ""
        if title.strip().lower().startswith("[ainews]"):
            continue  # Latent Space tygodniowy digest: kilka newsów w jednym wpisie
        is_promo = item.get("source_id") in PROMO_SOURCES
        if not is_promo and (not item["content"] or len(item["content"]) < 600):
            # RSS bez/prawie bez treści (HF, blog.google, HN=link "Comments",
            # zajawki <600ch): news potrzebuje pełnego tekstu, promocje zostawiamy.
            text = _oschina_api_text(client, item["url"])
            if text:
                item["content"] = text
            else:
                try:
                    resp = client.get(item["url"], timeout=20, follow_redirects=True)
                    resp.raise_for_status()
                    text = extract_article_text(resp.content)
                    figures = extract_article_images(resp.content, base_url=str(resp.url))
                    item["content"] = text + ("\n\n" + "\n\n".join(figures) if figures else "")
                except httpx.HTTPError:
                    pass
                if settings is not None and len(item["content"] or "") < 200:
                    # SPA/blokada: render JS przez Firecrawl (budżet kredytów pilnuje store).
                    md = firecrawl_scrape(settings, item["url"], store)
                    if len(md) > len(item["content"] or ""):
                        item["content"] = md
        if len(item["content"]) < 40 and not is_promo:
            item["content"] = item["title"]  # news bez treści: fallback do tytułu
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
    last = None
    # TLS handshake timeout (qbitai) potrafi trwać >1s: wykładniczy backoff.
    for attempt in range(4):
        try:
            response = client.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": _UA})
            response.raise_for_status()
            return parse_feed(response.content)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.ReadTimeout) as exc:
            last = exc
            time.sleep(2 ** attempt)  # 1, 2, 4, 8 s
        except httpx.HTTPError as exc:
            raise FetchError(f"failed to fetch feed: {exc}") from exc
    raise FetchError(f"failed to fetch feed after retries: {last}") from last


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
