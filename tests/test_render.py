import pytest

from aifeed.render import slugify, render_feed, render_article, write_site
from aifeed.store import Store


def test_write_site_creates_rss_with_article_links(tmp_path):
    store = Store(str(tmp_path / "db"))
    store.add_article("news", "Tytuł", "Lead", "Treść", "modele", [], None, None, [], 1.0, None)
    output = tmp_path / "site"
    write_site(store, str(output), site_url="https://ai.gradzik.org")
    rss = (output / "rss.xml").read_text()
    assert "<rss" in rss
    assert "https://ai.gradzik.org/a/news.html" in rss
    assert "Tytuł" in rss


def test_write_site_creates_feed_and_article(tmp_path):
    store = Store(str(tmp_path / "db"))
    store.add_article("news", "Tytuł", "Lead", "Treść", "modele", [{"name": "S", "url": "https://example.org"}], None, None, [], 1.0, None)
    output = tmp_path / "site"
    write_site(store, str(output))
    assert "Tytuł" in (output / "index.html").read_text()
    assert "Treść" in (output / "a" / "news.html").read_text()
    assert "Tytuł" in (output / "kat" / "modele.html").read_text()
    assert "Tytuł" not in (output / "kat" / "cenniki.html").read_text()


def test_article_links_sources_and_previous_coverage():
    article = Article(2, "delta", "Nowe wyniki", "Lead", "Nowe fakty.", "modele",
                      [{"name": "Benchmark", "url": "https://example.org/bench"}], None, None, [], 2.0, 1, 2.0)
    previous = Article(1, "original", "Pierwszy artykuł", "Lead", "Body", "modele", [], None, None, [], 1.0, None, 1.0)
    html = render_article(article, previous)
    assert 'href="https://example.org/bench"' in html
    assert 'href="/a/original.html"' in html
    assert "Nowe fakty." in html
from aifeed.store import Article


def test_feed_shows_summary_only_when_provided():
    article = Article(1, "news", "Tytuł", "Lead", "Body", "modele", [], None, None, [], 1.0, None, 1.0)
    assert "w skrócie" not in render_feed([article])
    assert "Podsumowanie dnia." in render_feed([article], summary="Podsumowanie dnia.")


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


def test_slugify_polish_title():
    assert slugify("Nowy model: zmiany cen API") == "nowy-model-zmiany-cen-api"


def test_slugify_rejects_empty_title():
    with pytest.raises(ValueError):
        slugify("???")
