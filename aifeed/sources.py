"""RSS and Atom sources. Other source types require separate adapters."""

RSS_SOURCES = {
    "openai": "https://openai.com/news/rss.xml",
    "codex_changelog": "https://developers.openai.com/codex/changelog/rss.xml",
    "deepmind": "https://deepmind.google/blog/rss.xml",
    "google_ai": "https://blog.google/technology/ai/rss/",
    "mistral": "https://mistral.ai/news/rss",
    "claude_code": "https://github.com/anthropics/claude-code/releases.atom",
    "opencode": "https://github.com/sst/opencode/releases.atom",
    "copilot_changelog": "https://github.blog/changelog/label/copilot/feed/",
    "copilot_cli": "https://github.com/github/copilot-cli/releases.atom",
    "anthropic_skills": "https://github.com/anthropics/skills/commits/main.atom",
    "superpowers": "https://github.com/obra/superpowers/commits/main.atom",
    "hacker_news": "https://news.ycombinator.com/rss",
    "simon_willison": "https://simonwillison.net/atom/everything/",
    "huggingface": "https://huggingface.co/blog/feed.xml",
    "ars_technica": "https://arstechnica.com/ai/feed/",
    "the_register": "https://www.theregister.com/software/ai_ml/headlines.atom",
    "import_ai": "https://jack-clark.net/feed/",
    "interconnects": "https://www.interconnects.ai/feed",
    "latent_space": "https://www.latent.space/feed",
    "purepc": "https://www.purepc.pl/rss.xml",
    "qwen_blog": "https://qwenlm.github.io/blog/index.xml",
    "leiphone": "https://www.leiphone.com/feed",
    "qbitai": "https://www.qbitai.com/feed",
    "oschina": "https://www.oschina.net/news/rss",
    "aichina_news": "https://www.aichina.news/rss.xml",
    "baoyu": "https://baoyu.io/feed.xml",
    "qwen_code": "https://github.com/QwenLM/qwen-code/releases.atom",
    "deepseek_harness": "https://github.com/deepseek-ai/deepseek-harness/releases.atom",
}

HTML_SOURCES = {
    "cursor_changelog": "https://cursor.com/changelog",
    "deepseek_updates": "https://api-docs.deepseek.com/updates",
}

API_SOURCES = {
    "hf_qwen": "https://huggingface.co/api/models?author=Qwen&sort=createdAt&direction=-1&limit=100",
    "hf_zhipu": "https://huggingface.co/api/models?author=zai-org&sort=createdAt&direction=-1&limit=100",
}

# Katalogi promocji: feed trzyma "aktywne oferty" (pubDate = start oferty,
# nie data aktualizacji), więc nie filtrujemy ich po dacie publikacji.
PROMO_SOURCES = {
    "aitkradar_deals": "https://aitkradar.cn/deals.xml",
    "aitkradar_changes": "https://aitkradar.cn/changes.xml",
    "tokentab": "https://www.tokentab.cn/feed.xml",
    "ai_deals_aggregator": "https://buguoshixc.github.io/ai-deals-aggregator/feed.xml",
}
