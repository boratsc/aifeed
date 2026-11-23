import pytest

import httpx

from aifeed.fetch import FetchError, fetch_feed, fetch_sources, ingest_feeds, parse_cursor, parse_deepseek, parse_feed, parse_hf_models


def test_hf_source_is_ingested(tmp_path):
    from aifeed.store import Store
    models = [{"id": "Qwen/Qwen-Test", "createdAt": "2026-11-20T06:30:00Z"}]
    store = Store(str(tmp_path / "db"))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=models))) as client:
        count, errors = ingest_feeds(client, store, {"hf_qwen": "https://huggingface.co/api/models?author=Qwen"}, 1795156200)
    assert count == 1
    assert errors == {}
    assert store.untriaged()[0].title == "Qwen/Qwen-Test"


def test_hf_models_produces_dated_first_party_model_cards():
    models = [{"id": "Qwen/Qwen-Test", "createdAt": "2026-11-20T06:30:00.000Z"}]
    item = parse_hf_models(models)[0]
    assert item["url"] == "https://huggingface.co/Qwen/Qwen-Test"
    assert item["published_at"] == 1795156200
    assert item["title"] == "Qwen/Qwen-Test"


def test_hf_models_skips_invalid_records():
    assert parse_hf_models([None, "bad", {"id": "../bad", "createdAt": "no-date"}]) == []


def test_deepseek_changelog_extracts_date_title_and_content():
    html = b'''<main><article><h2 id="nov">Date: 2026-11-20</h2>
    <h3>API update</h3><p>New features available.</p>
    <h2 id="oct">Date: 2026-10-01</h2><h3>Older update</h3><p>Old.</p></article></main>'''
    items = parse_deepseek(html)
    assert len(items) == 2
    assert items[0]["title"] == "API update"
    assert items[0]["url"] == "https://api-docs.deepseek.com/updates#nov"
    assert items[0]["published_at"] == 1795132800
    assert "New features available." in items[0]["content"]


def test_deepseek_source_is_ingested(tmp_path):
    from aifeed.store import Store
    html = b'''<main><article><h2 id="nov">Date: 2026-11-20</h2><h3>API update</h3><p>New.</p></article></main>'''
    store = Store(str(tmp_path / "db"))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=html))) as client:
        count, errors = ingest_feeds(client, store, {"deepseek_updates": "https://api-docs.deepseek.com/updates"}, 1795132800)
    assert count == 1
    assert errors == {}
    assert store.untriaged()[0].title == "API update"


def test_deepseek_changed_page_is_error():
    with pytest.raises(FetchError):
        parse_deepseek(b"<html>maintenance</html>")


def test_parse_cursor_reads_dated_articles():
    html = b'''<article><a href="/changelog/feature"><time datetime="2026-11-20T06:30:00Z">Nov 20</time></a>
    <h2><a href="/changelog/feature">New feature</a></h2><p>Details</p></article>'''
    item = parse_cursor(html)[0]
    assert item["url"] == "https://cursor.com/changelog/feature"
    assert item["title"] == "New feature"
    assert item["published_at"] == 1795156200


def test_cursor_source_is_fetched_and_ingested(tmp_path):
    html = b'''<article><time datetime="2026-11-20T06:30:00Z"></time>
    <h1><a href="/changelog/feature">New feature</a></h1></article>'''
    store = Store(str(tmp_path / "db"))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=html))) as client:
        count, errors = ingest_feeds(client, store, {"cursor_changelog": "https://cursor.com/changelog"}, 1795156200)
    assert count == 1
    assert errors == {}
    assert store.untriaged()[0].title == "New feature"


def test_parse_cursor_reports_changed_markup():
    with pytest.raises(FetchError):
        parse_cursor(b"<html><body>Changed</body></html>")
from aifeed.store import Store


def test_ingest_feeds_respects_cutoff_and_deduplicates(tmp_path):
    feed = b'''<rss version="2.0"><channel><title>T</title>
      <item><title>Fresh</title><link>https://example.org/fresh</link><pubDate>Fri, 20 Nov 2026 07:30:00 +0100</pubDate></item>
      <item><title>Old</title><link>https://example.org/old</link><pubDate>Mon, 16 Nov 2026 07:30:00 +0100</pubDate></item>
      <item><title>No date</title><link>https://example.org/unknown</link></item>
    </channel></rss>'''
    store = Store(str(tmp_path / "feed.db"))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=feed))) as client:
        sources = {"source": "https://example.org/feed"}
        assert ingest_feeds(client, store, sources, since_ts=1795156200) == (1, {})
        assert ingest_feeds(client, store, sources, since_ts=1795156200) == (0, {})
    assert [item.title for item in store.untriaged()] == ["Fresh"]


def test_fetch_sources_continues_after_failure():
    feed = b'<rss version="2.0"><channel><title>T</title><item><title>N</title><link>https://example.org/n</link></item></channel></rss>'
    def handler(request):
        return httpx.Response(404) if request.url.path == "/bad" else httpx.Response(200, content=feed)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        items, errors = fetch_sources(client, {"broken": "https://example.org/bad", "good": "https://example.org/feed"})
    assert items[0]["source_id"] == "good"
    assert "broken" in errors
    assert len(items) == 1


def test_fetch_feed_returns_parsed_items():
    feed = b'<rss version="2.0"><channel><title>T</title><item><title>N</title><link>https://example.org/n</link></item></channel></rss>'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=feed))) as client:
        assert fetch_feed(client, "https://example.org/rss")[0]["title"] == "N"


def test_fetch_feed_reports_http_error():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        with pytest.raises(FetchError):
            fetch_feed(client, "https://example.org/rss")


def test_fetch_feed_reports_timeout():
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(FetchError):
            fetch_feed(client, "https://example.org/rss")


def test_parse_feed_reports_html_error_page():
    with pytest.raises(FetchError):
        parse_feed(b"<html><body>Access denied</body></html>")


def test_parse_feed_allows_valid_empty_feed():
    assert parse_feed(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>') == []


def test_parse_feed_keeps_link_and_decodes_html():
    feed = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
      <item><title>New model</title><link>https://example.org/news/1</link>
      <description>&lt;p&gt;A &amp;amp; B&lt;/p&gt;</description></item>
    </channel></rss>'''
    items = parse_feed(feed)
    assert len(items) == 1
    assert items[0]["url"] == "https://example.org/news/1"
    assert items[0]["title"] == "New model"
    assert items[0]["content"] == "A & B"
    assert items[0]["og_image"] is None


def test_parse_feed_extracts_media_thumbnail():
    feed = b'''<rss xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel><title>T</title>
      <item><title>N</title><link>https://example.org/n</link>
      <media:thumbnail url="https://example.org/img.jpg"/></item></channel></rss>'''
    assert parse_feed(feed)[0]["og_image"] == "https://example.org/img.jpg"


def test_feed_publication_date_is_utc_timestamp():
    feed = b'''<rss version="2.0"><channel><title>Test</title>
      <item><title>News</title><link>https://example.org/1</link>
      <pubDate>Fri, 20 Nov 2026 07:30:00 +0100</pubDate></item>
    </channel></rss>'''
    assert parse_feed(feed)[0]["published_at"] == 1795156200


def test_feed_without_publication_date_has_no_timestamp():
    feed = b'''<rss version="2.0"><channel><title>Test</title>
      <item><title>News</title><link>https://example.org/1</link></item>
    </channel></rss>'''
    assert parse_feed(feed)[0]["published_at"] is None


def test_parse_feed_rejects_items_without_http_link():
    feed = b'''<rss version="2.0"><channel><title>Test</title>
      <item><title>Bad</title><link>javascript:alert(1)</link></item>
    </channel></rss>'''
    assert parse_feed(feed) == []


def test_extract_article_text_prefers_prose_over_main():
    from aifeed.fetch import extract_article_text
    html = b"<html><body><main>nav junk <div class='prose'><p>Prawdziwa tresc artykulu.</p></div> footer</main></body></html>"
    assert extract_article_text(html) == "Prawdziwa tresc artykulu."


def test_extract_article_text_falls_back_to_article_then_main():
    from aifeed.fetch import extract_article_text
    assert extract_article_text(b"<main>x<article>Tekst</article>y</main>") == "Tekst"
    assert extract_article_text(b"<main>Tylko main</main>") == "Tylko main"


def test_extract_article_text_returns_empty_on_garbage():
    from aifeed.fetch import extract_article_text
    assert extract_article_text(b"<html><body>nic</body></html>") == ""


def test_ingest_backfills_empty_content_from_article_page():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>T</title><link>https://example.org/post</link>
    <pubDate>Fri, 20 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>"""
    page = b"<html><main><div class='prose'><p>Pelna tresc publikacji, ktora jest dostatecznie dluga, zeby nie byc uznana za zajawke.</p></div></main></html>"
    def handler(request):
        if str(request.url) == "https://example.org/feed":
            return httpx.Response(200, content=feed)
        return httpx.Response(200, content=page)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"src": "https://example.org/feed"}, 0)
    row = store.db.execute("SELECT content FROM raw").fetchone()
    assert row["content"] == "Pelna tresc publikacji, ktora jest dostatecznie dluga, zeby nie byc uznana za zajawke."


def test_ingest_keeps_rss_content_when_present():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    long = "Pelna tresc z RSS " * 60  # >600 znaków, news nie odpala backfillu
    feed = ('<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>'
            '<item><title>T</title><link>https://example.org/post</link>'
            '<pubDate>Fri, 20 Nov 2026 00:00:00 GMT</pubDate>'
            '<description>' + long + '</description></item></channel></rss>').encode('utf-8')
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=feed)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"src": "https://example.org/feed"}, 0)
    assert calls == ["https://example.org/feed"]  # bez dodatkowego fetcha strony
    content = store.db.execute("SELECT content FROM raw").fetchone()["content"]
    assert content.startswith("Pelna tresc z RSS")
    assert len(content) > 600


def test_extract_article_images_skips_avatars():
    from aifeed.fetch import extract_article_images
    html = b"""<main><div class='prose'>
    <img src='https://x.dev/avatars/a.png' alt='author avatar'>
    <img src='https://cdn.example.com/fig1.png' alt='Wyniki Qwen na 442 zadaniach'>
    <img src='javascript:alert(1)' alt='zly'>
    </div></main>"""
    assert extract_article_images(html) == ["![Wyniki Qwen na 442 zadaniach](https://cdn.example.com/fig1.png)"]


def test_extract_article_images_empty_without_figures():
    from aifeed.fetch import extract_article_images
    assert extract_article_images(b"<main><p>sam tekst</p></main>") == []


def test_ingest_appends_figure_markdown_to_backfilled_content():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>T</title><link>https://example.org/post</link>
    <pubDate>Fri, 20 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>"""
    page = b"""<html><main><div class='prose'><p>Tekst publikacji.</p>
    <img src='https://cdn.example.com/fig.png' alt='Wykres wynikow'>
    <img src='https://x.dev/avatars/a.png' alt='autor'></div></main></html>"""
    def handler(request):
        if str(request.url).endswith("/feed"):
            return httpx.Response(200, content=feed)
        return httpx.Response(200, content=page)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"src": "https://example.org/feed"}, 0)
    content = store.db.execute("SELECT content FROM raw").fetchone()["content"]
    assert "Tekst publikacji." in content
    assert "![Wykres wynikow](https://cdn.example.com/fig.png)" in content


def test_ingest_backfills_trivial_content_too():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>T</title><link>https://example.org/post</link>
    <pubDate>Fri, 20 Nov 2026 00:00:00 GMT</pubDate>
    <description>Comments</description></item></channel></rss>"""
    page = b"<html><article><p>Pelna tresc artykulu z linku, ktora jest dostatecznie dluga by nie byc zajawka.</p></article></html>"
    def handler(request):
        if str(request.url).endswith("/feed"):
            return httpx.Response(200, content=feed)
        return httpx.Response(200, content=page)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"src": "https://example.org/feed"}, 0)
    assert store.db.execute("SELECT content FROM raw").fetchone()["content"] == "Pelna tresc artykulu z linku, ktora jest dostatecznie dluga by nie byc zajawka."


def test_fetch_feed_sends_user_agent():
    import httpx
    from aifeed.fetch import fetch_feed
    captured = {}
    def handler(request):
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, content=b"<?xml version=\"1.0\"?><rss version=\"2.0\"><channel><title>f</title><item><title>T</title><link>https://x.example/</link><pubDate>Fri, 20 Nov 2026 00:00:00 GMT</pubDate><description>d</description></item></channel></rss>")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch_feed(client, "https://x.example/feed")
    assert captured["ua"] and "Mozilla" in captured["ua"]


def test_ingest_skips_ainews_digests():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>[AINews] Riemann + Gemini + Inkling digest</title><link>https://x/1</link>
    <pubDate>Fri, 21 Nov 2026 00:00:00 GMT</pubDate><description>multi topic</description></item>
    <item><title>Real single news about an API</title><link>https://x/2</link>
    <pubDate>Fri, 21 Nov 2026 00:00:00 GMT</pubDate><description>single topic description</description></item>
    </channel></rss>"""
    def handler(request):
        return httpx.Response(200, content=feed)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"s": "https://x/feed"}, 0)
    titles = [r["title"] for r in store.db.execute("SELECT title FROM raw")]
    assert "[AINews]" not in " ".join(titles)
    assert "Real single news about an API" in titles


def test_ingest_backfills_short_news_not_just_empty():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>T</title><link>https://x/news/1</link>
    <pubDate>Fri, 21 Nov 2026 00:00:00 GMT</pubDate>
    <description>Krotka zajawka, ktora ma wiecej niz 40 ale mniej niz 600 znakow.</description>
    </item></channel></rss>"""
    page = b"<html><article><p>Pelna tresc artykulu, ktora jest dluga i prawdziwa, z faktami i liczbami oraz kontekstem tematu.</p></article></html>"
    def handler(request):
        if str(request.url).endswith("/feed"):
            return httpx.Response(200, content=feed)
        return httpx.Response(200, content=page)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"openai": "https://x/feed"}, 0)
    content = store.db.execute("SELECT content FROM raw").fetchone()["content"]
    assert "Pelna tresc artykulu" in content


def test_ingest_does_not_backfill_promo_sources():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = ('<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>'
            '<item><title>Promocja Qwen 2折</title><link>https://aitkradar.cn/deals/x</link>'
            '<pubDate>Fri, 21 Nov 2026 00:00:00 GMT</pubDate>'
            '<description>限时特价 截止2026-11-25</description>'
            '</item></channel></rss>').encode('utf-8')
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=feed)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"aitkradar_deals": "https://aitkradar.cn/deals.xml"}, 0)
    assert calls == ["https://aitkradar.cn/deals.xml"]  # brak fetchu strony deala
    content = store.db.execute("SELECT content FROM raw").fetchone()["content"]
    assert "截止" in content


def test_fetch_feed_retries_transient_ssl_error():
    import aifeed.fetch as fetch
    import httpx
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("SSL: UNEXPECTED_EOF_WHILE_READING")
        body = b"<?xml version='1.0'?><rss version='2.0'><channel><title>f</title><item><title>T</title><link>https://x/1</link><pubDate>Fri, 21 Nov 2026 00:00:00 GMT</pubDate><description>d</description></item></channel></rss>"
        return httpx.Response(200, content=body, request=httpx.Request("GET", "https://x/feed"))
    monkeypatch_time = None
    import time as _t
    real_sleep = _t.sleep
    class FakeClient:
        def get(self, url, **kw):
            return handler(url)
    # patch httpx.Client + sleep
    import types
    # simpler: monkeypatch fetch.time.sleep
    slept = []
    orig = getattr(fetch, "time", None)
    fetch.time = types.SimpleNamespace(sleep=lambda s: slept.append(s))
    try:
        items = fetch.fetch_feed(FakeClient(), "https://x/feed")
    finally:
        if orig is not None:
            fetch.time = orig
    assert len(items) == 1
    assert calls["n"] == 2
    assert slept


def test_extract_article_text_reads_markdown_body():
    from aifeed.fetch import extract_article_text
    html = b"<html><body><div class='markdown-body'><p>Prawdziwa tresc README z Githuba.</p><p>Drugi akapit z faktami.</p></div></body></html>"
    assert "Prawdziwa tresc README" in extract_article_text(html)


def test_extract_article_text_falls_back_to_longest_content_element():
    from aifeed.fetch import extract_article_text
    html = (b"<html><body><div id='nav'>menu</div>"
            b"<div id='art'><p>Glowna dluga tresc artykulu, ktorej nie ma zadnego znanego selektora.</p></div></body></html>")
    out = extract_article_text(html)
    assert "Glowna dluga tresc" in out


def test_extract_article_images_resolves_relative_src():
    from aifeed.fetch import extract_article_images
    html = b"<html><body><div class='markdown-body'><img src='docs/images/opening.png' alt='Opening screen'></div></body></html>"
    assert extract_article_images(html, base_url="http://ricapar.net/ClawDOS/") == \
        ["![Opening screen](http://ricapar.net/ClawDOS/docs/images/opening.png)"]


def test_extract_article_images_keeps_http_src():
    from aifeed.fetch import extract_article_images
    html = b"<div class='markdown-body'><img src='https://x/y.png' alt='A'></div>"
    assert extract_article_images(html) == ["![A](https://x/y.png)"]


def test_backfill_uses_oschina_api_for_spa_pages():
    import httpx
    from aifeed.fetch import ingest_feeds
    from aifeed.store import Store
    import tempfile, os, json
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>Genie 4</title><link>https://www.oschina.net/news/503750/google-deepmind-genie-4</link>
    <pubDate>Mon, 23 Nov 2026 00:00:00 GMT</pubDate>
    <description>krotka zajawka</description></item></channel></rss>"""
    spa = b"<html><head><script src='vite.js'></script></head><body></body></html>"
    api_body = json.dumps({"code": 1, "obj_data": {"body": "<p>Pelna tresc newsa z API oschina, dostatecznie dluga aby przejsc prog.</p>" + "<p>x</p>"*30}}).encode()
    def handler(request):
        u = str(request.url)
        if "news_detail" in u:
            assert "id=503750" in u
            return httpx.Response(200, content=api_body)
        if u.endswith("/rss"):
            return httpx.Response(200, content=feed)
        return httpx.Response(200, content=spa)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"oschina": "https://www.oschina.net/news/rss"}, 0)
    content = store.db.execute("SELECT content FROM raw").fetchone()["content"]
    assert "Pelna tresc newsa z API oschina" in content


def test_firecrawl_scrape_returns_markdown(monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    s = Settings(firecrawl_key="fc-test", firecrawl_budget=100)
    captured = {}
    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"success": True, "data": {"markdown": "# Tytul\n\nTresc strony."}})
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    md = fetch.firecrawl_scrape(s, "https://example.org/spa")
    assert md == "# Tytul\n\nTresc strony."
    assert captured["auth"] == "Bearer fc-test"
    assert "scrape" in captured["url"]


def test_firecrawl_scrape_disabled_without_key():
    import aifeed.fetch as fetch
    from aifeed.config import Settings
    assert fetch.firecrawl_scrape(Settings(firecrawl_key=""), "https://x") == ""


def test_firecrawl_scrape_respects_budget():
    import aifeed.fetch as fetch
    from aifeed.config import Settings
    s = Settings(firecrawl_key="fc-test", firecrawl_budget=0)
    assert fetch.firecrawl_scrape(s, "https://x") == ""


def test_ingest_uses_firecrawl_when_page_extraction_fails(monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.store import Store
    import tempfile, os
    store = Store(os.path.join(tempfile.mkdtemp(), "db"))
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>T</title><link>https://spa.example/post</link>
    <pubDate>Mon, 23 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>"""
    def handler(request):
        if str(request.url).endswith("/feed"):
            return httpx.Response(200, content=feed)
        return httpx.Response(200, content=b"<html><body><script>spa</script></body></html>")
    monkeypatch.setattr(fetch, "firecrawl_scrape", lambda s, url, store=None: "Pelna tresc strony wyrenderowana przez firecrawl, dostatecznie dluga.")
    from aifeed.config import Settings
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch.ingest_feeds(client, store, {"src": "https://spa.example/feed"}, 0, Settings(firecrawl_key="fc-test"))
    assert "wyrenderowana przez firecrawl" in store.db.execute("SELECT content FROM raw").fetchone()["content"]


def test_research_uses_firecrawl_then_sonAR(monkeypatch):
    import aifeed.fetch as fetch
    from aifeed.config import Settings
    calls = []
    monkeypatch.setattr(fetch, 'firecrawl_search', lambda settings, query, store=None: calls.append('firecrawl') or ['https://facebook.com/post'])
    monkeypatch.setattr(fetch, 'sonar_search', lambda settings, query, store=None: calls.append('sonar') or ['https://other.example/source'])
    result, _ = fetch.research_sources(Settings(firecrawl_key='f', sonar_key='s'), 'A news title')
    assert result == ['https://facebook.com/post', 'https://other.example/source']
    assert calls == ['firecrawl', 'sonar']


def test_firecrawl_search_reads_web_urls(monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    def handle(request):
        assert request.headers['authorization'] == 'Bearer test'
        return httpx.Response(200, json={'data': {'web': [{'url': 'https://example.org/a'}]}})
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle)))
    assert fetch.firecrawl_search(Settings(firecrawl_key='test'), 'news') == ['https://example.org/a']


def test_sonar_search_extracts_primary_urls(monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    def handle(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': 'https://example.org/doc\nhttps://example.org/blog'}}]})
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle)))
    assert fetch.sonar_search(Settings(sonar_key='test'), 'news') == ['https://example.org/doc', 'https://example.org/blog']


def test_research_enriches_article_only_from_verified_sources(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('news', 'Jync v2.4.5 synchronizacja komentarzy i priorytet kursorow', 'Lead', 'Tresc bez benchmarku.', 'modele', [{'name': 's', 'url': 'https://example.org/'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda s, q, st=None: (['https://official.example/doc', 'https://news.example/duplicate'], ['Firecrawl']))
    def get(request, **kw):
        if str(request.url) == 'https://official.example/doc':
            return httpx.Response(200, content=b'<main><div class="prose"><p>Jync v2.4.5 dodaje synchronizacje komentarzy i priorytet kursorow, poprawki dla tabel bez klucza glownego oraz komendy powtarzalnego wydania.</p></div></main>')
        return httpx.Response(200, content=b'<html><body>nav only</body></html>')
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(get)))
    s = Settings()
    fetch.enrich_article_with_research(s, store, aid)
    text = store.article_by_id(aid).body_pl
    assert 'Jync v2.4.5 dodaje synchronizacje komentarzy' in text
    assert 'https://official.example/doc' in text
    assert 'https://news.example/duplicate' not in text
    assert text.count('---') == 1


def test_research_calls_sonar_for_social_secondary_results(monkeypatch):
    import aifeed.fetch as fetch
    from aifeed.config import Settings
    monkeypatch.setattr(fetch, 'firecrawl_search', lambda *a: ['https://x.com/status/1'])
    monkeypatch.setattr(fetch, 'sonar_search', lambda *a: ['https://official.example/doc'])
    assert fetch.research_sources(Settings(), 'n') == (['https://x.com/status/1', 'https://official.example/doc'], ['Firecrawl', 'Sonar'])


def test_research_skips_sonar_for_primary_results(monkeypatch):
    import aifeed.fetch as fetch
    from aifeed.config import Settings
    monkeypatch.setattr(fetch, 'firecrawl_search', lambda *a: ['https://official.example/doc'])
    monkeypatch.setattr(fetch, 'sonar_search', lambda *a: (_ for _ in ()).throw(AssertionError('Sonar unnecessarily called')))
    assert fetch.research_sources(Settings(), 'n') == (['https://official.example/doc'], ['Firecrawl'])


def test_research_rejects_unrelated_page(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('jync', 'Jync v2.4.5: synchronizacja komentarzy i priorytet kursorow', 'Nowe wydanie', 'Opis wydania.', 'narzedzia', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: (['https://planetmainframe.com/db2'], ['Firecrawl']))
    body = ('SQL and SQL PL enhancements in Db2 12 and 13 are described here at length for database '
            'administrators who upgrade their mainframe systems. ' * 6)
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=f'<main><div class="prose"><p>{body}</p></div></main>'.encode()))))
    assert fetch.enrich_article_with_research(Settings(), store, aid) is False
    assert store.article_by_id(aid).body_pl == 'Opis wydania.'


def test_research_dry_run_reports_without_writing(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('n', 'Jync v2.4.5 synchronizacja komentarzy i priorytet kursorow', 'Lead', 'Opis.', 'narzedzia', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: (['https://github.com/vfaner/jync/releases'], ['Firecrawl']))
    body = ('Jync v2.4.5 dodaje synchronizacje komentarzy i priorytet kursorow w interfejsie, '
            'a takze poprawki dla tabel bez klucza glownego. ' * 5)
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=f'<main><div class="prose"><p>{body}</p></div></main>'.encode()))))
    result = fetch.enrich_article_with_research(Settings(), store, aid, dry_run=True)
    assert result['url'] == 'https://github.com/vfaner/jync/releases'
    assert result['chars'] >= 400
    assert store.article_by_id(aid).body_pl == 'Opis.'


def test_relevance_rejects_other_product_with_shared_generic_words():
    from aifeed.fetch import is_relevant
    title = 'Jync v2.4.5: synchronizacja komentarzy i priorytet kursorow'
    page = ('Jync to narzedzie do synchronizacji danych z priorytetem kursorow, '
            'ktore opisuje synchronizacje komentarzy w innych produktach. ' * 4)
    assert is_relevant(title, page) is True
    page_db2 = ('Db2 12 and 13 add SQL enhancements; administrators synchronizuja komentarzy '
                'i ustawiaja priorytet w kolejkach. ' * 5)
    assert is_relevant(title, page_db2) is False


def test_relevance_ignores_numeric_only_match():
    from aifeed.fetch import is_relevant
    assert is_relevant('Edge0-145B AutoArk laptop', 'Unrelated post about 145B and 2026 conference schedule') is False


def test_enrichment_skips_social_links(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('upstage', 'Upstage Solar Mini 4.5 Preview w cenniku API', 'Lead', 'Opis.', 'cenniki', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: (['https://x.com/upstageai/status/1'], ['Firecrawl']))
    body = 'Upstage Solar Mini 4.5 Preview pricing details for cached input tokens. ' * 5
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=f'<main><div class="prose"><p>{body}</p></div></main>'.encode()))))
    assert fetch.enrich_article_with_research(Settings(), store, aid) is False


def test_enrichment_prefers_official_domain(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('u', 'Upstage Solar Mini 4.5 Preview w cenniku API', 'Lead', 'Opis.', 'cenniki', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: (['https://randomblog.example/post', 'https://console.upstage.ai/docs/models/solar-mini-4-5-preview'], ['Firecrawl']))
    body = 'Upstage Solar Mini 4.5 Preview pricing details for cached input tokens. ' * 5
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=f'<main><div class="prose"><p>{body}</p></div></main>'.encode()))))
    r = fetch.enrich_article_with_research(Settings(), store, aid, dry_run=True)
    assert r['url'] == 'https://console.upstage.ai/docs/models/solar-mini-4-5-preview'


def test_research_materials_for_article_marks_flag(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('upstage', 'Upstage Solar Mini 4.5 Preview w cenniku API', 'Lead', 'Opis.', 'cenniki', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: (['https://www.upstage.ai/pricing/api'], ['Firecrawl']))
    body = 'Upstage Solar Mini 4.5 Preview cached input pricing is listed here in detail. ' * 4
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=f'<main><div class="prose"><p>{body}</p></div></main>'.encode()))))
    mats = fetch.research_materials(Settings(), store, aid)
    assert len(mats) == 1
    assert mats[0]['research'] is True
    assert mats[0]['url'] == 'https://www.upstage.ai/pricing/api'
    assert 'cached input pricing' in mats[0]['content']
    assert store.article_by_id(aid).body_pl == 'Opis.'


def test_research_materials_empty_when_nothing_matches(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('j', 'Jync v2.4.5 synchronizacja komentarzy', 'Lead', 'Opis.', 'narzedzia', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: ([], []))
    assert fetch.research_materials(Settings(), store, aid) == []


def test_research_materials_returns_multiple_relevant_sources(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('upstage', 'Upstage Solar Mini 4.5 Preview w cenniku API', 'Lead', 'Opis.', 'cenniki', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: ([
        'https://www.upstage.ai/pricing/api',
        'https://console.upstage.ai/docs/models/solar-mini-4-5-preview',
    ], ['Firecrawl']))
    body = 'Upstage Solar Mini 4.5 Preview pricing and model card details are documented here. ' * 3
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=f'<main><div class="prose"><p>{body}</p></div></main>'.encode()))))
    mats = fetch.research_materials(Settings(), store, aid, limit=2)
    assert [m['url'] for m in mats] == [
        'https://www.upstage.ai/pricing/api',
        'https://console.upstage.ai/docs/models/solar-mini-4-5-preview',
    ]


def test_research_sources_returns_used_engines(monkeypatch):
    import aifeed.fetch as fetch
    from aifeed.config import Settings
    monkeypatch.setattr(fetch, 'firecrawl_search', lambda *a, **k: ['https://example.org/doc'])
    monkeypatch.setattr(fetch, 'sonar_search', lambda *a, **k: ['https://x.com/status/1'])
    urls, engines = fetch.research_sources(Settings(firecrawl_key='f'), 'n')
    assert urls == ['https://example.org/doc']
    assert engines == ['Firecrawl']


def test_research_engines_are_saved_for_article(tmp_path, monkeypatch):
    import aifeed.fetch as fetch
    import httpx
    from aifeed.config import Settings
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    aid = store.add_article('u', 'Upstage Solar Mini 4.5 Preview w cenniku API', 'Lead', 'Opis.', 'cenniki', [{'name': 's', 'url': 'https://ex.org'}], None, None, [], 1, None)
    monkeypatch.setattr(fetch, 'research_sources', lambda *a, **k: (['https://www.upstage.ai/pricing/api'], ['Firecrawl', 'Sonar']))
    body = 'Upstage Solar Mini 4.5 Preview cached input pricing is listed here in detail. ' * 4
    real_client = httpx.Client
    monkeypatch.setattr(fetch.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=f'<main><div class="prose"><p>{body}</p></div></main>'.encode()))))
    materials = fetch.research_materials(Settings(), store, aid)
    assert materials[0]['research_engines'] == ['Firecrawl', 'Sonar']
    assert store.research_engines() == {}
