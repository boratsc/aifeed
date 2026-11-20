from urllib.parse import urlparse

from aifeed.sources import RSS_SOURCES


def test_rss_sources_have_unique_names_and_https_urls():
    assert len(RSS_SOURCES) >= 10
    assert len(RSS_SOURCES) == len(set(RSS_SOURCES))
    assert all(urlparse(url).scheme == "https" for url in RSS_SOURCES.values())


def test_sources_include_selected_coding_and_independent_feeds():
    assert {"openai", "claude_code", "opencode", "simon_willison", "purepc", "copilot_changelog", "copilot_cli"} <= RSS_SOURCES.keys()
