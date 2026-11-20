import pytest

from aifeed.render import slugify, render_feed, render_article, write_site
from aifeed.store import Store


def test_feed_shows_thumbnail_when_article_has_image():
    from aifeed.store import Article
    article = Article(1, "news", "Tytuł", "Lead", "Body", "modele", [], None, None, [],
                      published_at=1.0, image="news.jpg")
    html = render_feed([article])
    assert 'src="/img/news.jpg"' in html


def test_feed_omits_thumbnail_when_no_image():
    from aifeed.store import Article
    article = Article(1, "news", "Tytuł", "Lead", "Body", "modele", [], None, None, [],
                      published_at=1.0)
    html = render_feed([article])
    assert '<img' not in html


def test_write_site_paginates_older_news(tmp_path):
    store = Store(str(tmp_path / "db"))
    for i in range(31):
        store.add_article(f"news-{i}", f"Tytuł {i}", "Lead", "Treść", "modele", [], None, None, [], 1000.0-i, None)
    output = tmp_path / "site"
    write_site(store, str(output))
    assert "Tytuł 0" in (output / "index.html").read_text()
    assert "Tytuł 30" not in (output / "index.html").read_text()
    assert "Tytuł 30" in (output / "page" / "2.html").read_text()
    assert 'href="/page/2.html"' in (output / "index.html").read_text()


def test_write_site_creates_rss_with_article_links(tmp_path):
    store = Store(str(tmp_path / "db"))
    store.add_article("news", "Tytuł", "Lead", "Treść", "modele", [], None, None, [], 1.0, None)
    output = tmp_path / "site"
    write_site(store, str(output), site_url="https://ai.gradzik.org")
    rss = (output / "rss.xml").read_text()
    assert "<rss" in rss
    assert "https://ai.gradzik.org/a/news.html" in rss
    assert "Tytuł" in rss
    assert "<pubDate>Thu, 01 Jan 1970 00:00:01 +0000</pubDate>" in rss


def test_write_site_creates_feed_and_article(tmp_path):
    store = Store(str(tmp_path / "db"))
    store.add_article("news", "Tytuł", "Lead", "Treść", "modele", [{"name": "S", "url": "https://example.org"}], None, None, [], 1.0, None)
    output = tmp_path / "site"
    write_site(store, str(output))
    assert "Tytuł" in (output / "index.html").read_text()
    assert "Treść" in (output / "a" / "news.html").read_text()
    category_html = (output / "kat" / "modele.html").read_text()
    assert "Tytuł" in category_html
    assert 'class="active" href="/kat/modele.html"' in category_html
    assert 'class="active" href="/"' not in category_html
    assert "Tytuł" not in (output / "kat" / "cenniki.html").read_text()


def test_article_renders_markdown_body_as_html():
    from aifeed.store import Article
    article = Article(1, "x", "Title", "Lead", "## Sekcja\n\nAkapit z **pogrubieniem**.", "modele", [], None, None, [], 1, None, 1)
    html = render_article(article)
    assert "<h2>Sekcja</h2>" in html
    assert "<strong>pogrubieniem</strong>" in html
    assert "## Sekcja" not in html


def test_article_never_renders_javascript_source_link():
    from aifeed.store import Article
    article = Article(1, "x", "Title", "Lead", "Body", "modele",
                      [{"name": "Unsafe", "url": "javascript:alert(1)"}], None, None, [], 1, None, 1)
    assert 'href="javascript:' not in render_article(article)


def test_article_links_sources_and_previous_coverage():
    article = Article(2, "delta", "Nowe wyniki", "Lead", "Nowe fakty.", "modele",
                      [{"name": "Benchmark", "url": "https://example.org/bench"}], None, None, [], 2.0, 1, 2.0)
    previous = Article(1, "original", "Pierwszy artykuł", "Lead", "Body", "modele", [], None, None, [], 1.0, None, 1.0)
    html = render_article(article, previous)
    assert 'href="https://example.org/bench"' in html
    assert 'href="/a/original.html"' in html
    assert "Nowe fakty." in html
    assert 'name="viewport"' in html
    assert 'class="article-layout"' in html
from aifeed.store import Article


def test_feed_shows_summary_only_when_provided():
    article = Article(1, "news", "Tytuł", "Lead", "Body", "modele", [], None, None, [], 1.0, None, 1.0)
    assert "w skrócie" not in render_feed([article])
    assert "Podsumowanie dnia." in render_feed([article], summary="Podsumowanie dnia.")


def test_feed_rail_lists_tool_articles():
    from aifeed.store import Article
    tools = Article(1, "codex", "Codex CLI 0.171", "l", "b", "narzedzia", [], None, None, [], published_at=2000.0)
    model = Article(2, "glm", "GLM release", "l", "b", "modele", [], None, None, [], published_at=3000.0)
    html = render_feed([model, tools])
    assert "// narzędzia" in html
    assert "Codex CLI 0.171" in html
    assert 'href="/a/codex.html"' in html
    assert "GLM release" not in html.split("feed-rail", 1)[1]


def test_feed_has_accessible_responsive_structure():
    article = Article(1, "news", "Tytuł", "Lead", "Body", "modele", [], None, None, [], 1.0, None, 1.0)
    html = render_feed([article])
    assert 'name="viewport"' in html
    assert '<nav aria-label="Kategorie">' in html
    assert 'class="feed-layout"' in html
    assert 'min-width:0' in html
    assert 'overflow-wrap:anywhere' in html
    assert 'class="feed-list"' in html
    assert 'feed-rail' in html
    assert '<time class="meta" datetime="1970-01-01T00:00:01+00:00">01:00</time>' in html
    assert "prefers-reduced-motion" in html
    for category in ("Modele", "Narzędzia", "Open source", "Cenniki", "Badania", "Infrastruktura"):
        assert category in html


def test_render_feed_escapes_untrusted_title():
    article = Article(1, "news", '<script>alert("x")</script>', "Lead", "Body", "modele",
                      [{"name": "Źródło", "url": "https://example.org/1"}], None, None, [], 1.0, None, 1.0)
    html = render_feed([article])
    assert '<script>alert("x")</script>' not in html
    assert '&lt;script&gt;' in html
    assert 'href="/a/news.html"' in html


def test_tool_tag_encodes_at_sign_against_cloudflare_email_mangling():
    from aifeed.store import Article
    article = Article(1, "x", "T", "L", "B", "modele", [], None, None, [], 1.0, None, 1.0)
    tool_versions = [{"name": "Vercel AI SDK", "repo": "vercel/ai", "tag": "@ai-sdk/workflow-harness@1.0.188"}]
    html = render_feed([article], tool_versions=tool_versions)
    # A bare "@" in the middle of text is what Cloudflare Email Obfuscation rewrites.
    assert "@ai-sdk/workflow-harness@1.0.188" not in html
    assert "&#64;ai-sdk/workflow-harness&#64;1.0.188" in html


def test_slugify_polish_title():
    assert slugify("Nowy model: zmiany cen API") == "nowy-model-zmiany-cen-api"


def test_slugify_rejects_empty_title():
    with pytest.raises(ValueError):
        slugify("???")
