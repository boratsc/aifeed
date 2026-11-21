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
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>
    <item><title>T</title><link>https://example.org/post</link>
    <pubDate>Fri, 20 Nov 2026 00:00:00 GMT</pubDate>
    <description>Juz jest tresc z RSS - dostatecznie dluga aby nie odpalac backfillu strony.</description></item></channel></rss>"""
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=feed)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest_feeds(client, store, {"src": "https://example.org/feed"}, 0)
    assert calls == ["https://example.org/feed"]  # bez dodatkowego fetcha strony
    assert store.db.execute("SELECT content FROM raw").fetchone()["content"] == "Juz jest tresc z RSS - dostatecznie dluga aby nie odpalac backfillu strony."


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
