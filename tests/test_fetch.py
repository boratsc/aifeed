from aifeed.fetch import parse_feed


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


def test_parse_feed_rejects_items_without_http_link():
    feed = b'''<rss version="2.0"><channel><title>Test</title>
      <item><title>Bad</title><link>javascript:alert(1)</link></item>
    </channel></rss>'''
    assert parse_feed(feed) == []
