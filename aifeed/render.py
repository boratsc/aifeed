"""Static site rendering helpers."""
import re
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from jinja2 import Environment, select_autoescape
import markdown
import httpx


def render_feed(articles, summary=None, next_page=None, active_category=None, tool_versions=None):
    env = Environment(autoescape=select_autoescape(["html"]))
    # Encode "@" so Cloudflare's Email Obfuscation doesn't rewrite scoped tool tags.
    from markupsafe import Markup
    env.filters["deobfuscate"] = lambda s: Markup(str(s).replace("@", "&#64;"))
    env.filters["time"] = lambda ts: datetime.fromtimestamp(ts, ZoneInfo("Europe/Warsaw")).strftime("%H:%M")
    env.filters["iso"] = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat()
    tool_versions = tool_versions or []
    return env.from_string('''<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ai:///feed</title>
<style>:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0d060b;color:#fff;font:14px/1.5 "IBM Plex Sans",system-ui,sans-serif}a{color:#ff488b;text-decoration:none}a:hover{text-decoration:underline}a:focus-visible{outline:2px solid #ff488b;outline-offset:3px}.mono{font-family:"IBM Plex Mono",ui-monospace,monospace}.top{height:64px;border-bottom:1px solid #33202d;display:flex;align-items:center;padding:0 20px;gap:12px}.brand{color:#fff;font-size:17px}.cursor{width:8px;height:14px;background:#ff488b}.tagline{color:#9a8f97;font-size:13px}.top .rss{margin-left:auto;color:#9a8f97}.feed-layout{display:grid;grid-template-columns:minmax(0,1fr);min-width:0}.feed-layout>*{min-width:0}.news-item h2,.news-item p{overflow-wrap:anywhere}.categories{padding:24px 20px}.categories h2,.rail h2{font:11px "IBM Plex Mono",monospace;color:#9a8f97}.categories nav{display:flex;gap:16px;overflow:auto}.categories a{white-space:nowrap;color:#9a8f97;font:13px "IBM Plex Mono",monospace;padding:6px 0}.categories a.active{color:#fff}.feed-list{padding:0 20px 32px}.feed-list h1{font-size:20px;font-weight:500;margin:20px 0}.news-item{padding:20px 0;border-bottom:1px solid #33202d}.news-item h2{font-size:17px;font-weight:500;margin:0 0 8px}.news-item h2 a{color:#fff}.news-item p{color:#9a8f97;font-size:13.5px;margin:0 0 10px}.news-body{display:flex;gap:16px;align-items:flex-start}.news-body .thumb{width:96px;height:60px;object-fit:cover;border:1px solid #33202d;flex:0 0 auto}.meta{font:11px "IBM Plex Mono",monospace;color:#9a8f97}.rail{padding:24px 20px;color:#9a8f97}.rail p{color:#fff;font-size:13px}.tool-list{list-style:none;margin:16px 0 0;padding:0}.tool-list li{display:flex;align-items:baseline;gap:8px;padding:7px 0;border-bottom:1px solid #33202d;font:12px "IBM Plex Mono",monospace}.tool-name{color:#fff;flex:1;overflow-wrap:anywhere}.tool-tag{color:#ff488b;white-space:nowrap}.tool-link{color:#9a8f97}.footer{padding:20px;border-top:1px solid #33202d;color:#9a8f97;font:12px "IBM Plex Mono",monospace;display:flex;justify-content:space-between;gap:16px}@media(min-width:1000px){.top{padding:0 40px}.feed-layout{grid-template-columns:260px minmax(0,780px) minmax(240px,1fr);max-width:1440px;margin:auto}.categories{border-right:1px solid #33202d;padding:36px 30px}.categories nav{display:block}.categories a{display:block;padding:8px 10px;margin-left:-10px}.categories a.active{background:#1b0e17;border:1px solid #33202d}.feed-list{padding:36px 0 32px}.rail{border-left:1px solid #33202d;padding:36px 30px}.footer{padding:20px 40px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}</style>
</head><body><header class="top"><a class="brand mono" href="/">ai:///feed</a><span class="cursor" aria-hidden="true"></span><span class="tagline mono">/ news dla builderów</span><a class="rss mono" href="/rss.xml">rss.xml ↗</a></header>
<div class="feed-layout"><aside class="categories"><h2>FEED / KATEGORIE</h2><nav aria-label="Kategorie"><a {% if not active_category %}class="active"{% endif %} href="/">› Wszystko</a>{% for slug,label in [("modele","Modele"),("narzedzia","Narzędzia"),("open-source","Open source"),("cenniki","Cenniki"),("badania","Badania"),("infrastruktura","Infrastruktura"),("promocje","Promocje")] %}<a {% if active_category==slug %}class="active"{% endif %} href="/kat/{{ slug }}">· {{ label }}</a>{% endfor %}</nav></aside>
<main class="feed-list" id="content">{% if summary %}<aside class="summary"><span class="meta">~ w skrócie /</span><p>{{ summary }}</p></aside>{% endif %}<h1>Najnowsze</h1>{% for article in articles %}<article class="news-item"><div class="news-body">{% if article.image %}<a href="/a/{{ article.slug }}"><img class="thumb" src="/img/{{ article.image }}" alt="" loading="lazy"></a>{% endif %}<div><time class="meta" datetime="{{ article.published_at|iso }}">{{ article.published_at|time }}</time><h2><a href="/a/{{ article.slug }}">{{ article.title_pl }}</a></h2><p>{{ article.lead_pl }}</p><span class="meta">{{ article.category }}</span></div></div></article>{% endfor %}{% if next_page %}<a class="older" href="{{ next_page }}">+ pokaż więcej</a>{% endif %}</main>
<aside class="rail feed-rail"><h2>// narzędzia</h2><ul class="tool-list">{% for tool in tool_versions %}<li><span class="tool-name">{{ tool.name }}</span>{% if tool.tag %}<span class="tool-tag">{{ tool.tag | deobfuscate }}</span>{% else %}<span class="tool-tag">—</span>{% endif %}<a class="tool-link" href="https://github.com/{{ tool.repo }}/releases" rel="noopener noreferrer">↗</a></li>{% endfor %}</ul></aside></div>
<footer class="footer"><span>ai:///feed © 2026 · Tłumaczenie AI z materiałów źródłowych</span><span>no tracking · no noise · just signal</span></footer></body></html>''').render(articles=articles, summary=summary, next_page=next_page, active_category=active_category, tool_versions=tool_versions)


def render_article(article, previous=None, model=None):
    env = Environment(autoescape=select_autoescape(["html"]))
    def safe_source(source):
        url = source.get("url")
        if not isinstance(url, str) or any(ord(char) < 32 for char in url):
            return False
        try:
            parsed = urlsplit(url)
            return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            return False
    sources = [source for source in article.sources if isinstance(source, dict) and safe_source(source)]
    body_html = markdown.markdown(article.body_pl or "", extensions=["tables"])
    return env.from_string('''<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ article.title_pl }} | ai:///feed</title>
<style>*{box-sizing:border-box}body{margin:0;background:#0d060b;color:#fff;font:17px/1.65 system-ui,sans-serif}header{border-bottom:1px solid #33202d;padding:20px 40px}.article-layout{max-width:1120px;margin:auto;padding:40px 20px}article{max-width:760px}h1{font-size:clamp(28px,4vw,38px);line-height:1.2;font-weight:500}h2{font-size:20px;font-weight:500;margin-top:36px}.lead{color:#c9bcc5}.body{white-space:pre-wrap;overflow-wrap:anywhere}.meta{font:12px monospace;color:#9a8f97}a{color:#ff488b}a:focus-visible{outline:2px solid #ff488b;outline-offset:3px}li{margin:12px 0}.credit{float:right;font-size:11px}.body img{max-width:100%;height:auto;display:block;margin:16px 0;cursor:zoom-in}.lightbox{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:1000;cursor:zoom-out}.lightbox.open{display:flex}.lightbox img{max-width:94vw;max-height:94vh;object-fit:contain}@media(max-width:600px){header{padding:16px 20px}.article-layout{padding:28px 20px}}</style></head>
<body><header><a href="/">ai:///feed</a>{% if model %}<span class="meta credit">tłumaczył: {{ model }}</span>{% endif %}</header><main class="article-layout"><a href="/">← wróć do feedu</a><article><p class="meta">{{ article.category }}</p><h1>{{ article.title_pl }}</h1><p class="lead">{{ article.lead_pl }}</p>
<div class="body">{{ body_html | safe }}</div><p class="meta">Tłumaczenie AI z materiałów źródłowych{% if model %} · model: {{ model }}{% endif %}</p>{% if previous %}<section><h2>Wcześniej o tym temacie</h2>
<a href="/a/{{ previous.slug }}">{{ previous.title_pl }}</a></section>{% endif %}
<section><h2>Źródła</h2><ul>{% for source in sources %}
<li><a href="{{ source.url }}" rel="noopener noreferrer">{{ source.name }}</a></li>{% endfor %}</ul></section>
</article></main><div class="lightbox" id="lightbox"><img src="" alt=""></div><script>var lb=document.getElementById('lightbox');var lbi=lb.querySelector('img');document.querySelectorAll('.body img').forEach(function(i){i.addEventListener('click',function(){lbi.src=i.src;lbi.alt=i.alt;lb.classList.add('open');});});lb.addEventListener('click',function(){lb.classList.remove('open');});document.addEventListener('keydown',function(e){if(e.key==='Escape')lb.classList.remove('open');});</script></body></html>''').render(article=article, previous=previous, sources=sources, body_html=body_html, model=model)


def _fetch_images(articles, img_dir, client):
    img_path = Path(img_dir)
    img_path.mkdir(parents=True, exist_ok=True)
    for article in articles:
        url = article.image
        if not url or not url.startswith(("http://", "https://")):
            continue
        try:
            parsed = urlsplit(url)
            ext = Path(parsed.path).suffix or ".jpg"
            if ext.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
                ext = ".jpg"
            filename = f"{article.slug}{ext}"
            resp = client.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            if len(resp.content) > 2 * 1024 * 1024:
                continue
            (img_path / filename).write_bytes(resp.content)
            article.image = filename
        except Exception:
            article.image = None


def write_site(store, output_dir, site_url="https://ai.gradzik.org", img_dir=None):
    output = Path(output_dir)
    articles_dir = output / "a"
    articles_dir.mkdir(parents=True, exist_ok=True)
    articles = store.articles_since(0)
    if img_dir is None:
        img_dir = output / "img"
    with httpx.Client() as client:
        _fetch_images(articles, img_dir, client)
    from .tools import TOOLS
    versions = store.tool_versions()
    tool_panel = [{"name": t["name"], "repo": t["repo"], "tag": versions.get(t["repo"])} for t in TOOLS]
    # Promocje to oferty z datą ważności, nie newsy: mają własną kategorię/stronę,
    # nie śmiecą głównego feedu.
    news = [article for article in articles if article.category != "promocje"]
    pages = [news[i:i+30] for i in range(0, len(news), 30)] or [[]]
    (output / "index.html").write_text(render_feed(pages[0], next_page="/page/2" if len(pages)>1 else None, tool_versions=tool_panel), encoding="utf-8")
    pages_dir = output / "page"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for number, page in enumerate(pages[1:], 2):
        next_link = f"/page/{number+1}" if number < len(pages) else None
        page_dir = pages_dir / str(number)
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(render_feed(page, next_page=next_link, tool_versions=tool_panel), encoding="utf-8")
    categories_dir = output / "kat"
    categories_dir.mkdir(parents=True, exist_ok=True)
    for category in ("modele", "narzedzia", "open-source", "cenniki", "badania", "infrastruktura", "promocje"):
        filtered = [article for article in articles if article.category == category]
        cat_dir = categories_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        (cat_dir / "index.html").write_text(render_feed(filtered, active_category=category, tool_versions=tool_panel), encoding="utf-8")
    env = Environment(autoescape=True)
    from email.utils import format_datetime
    env.filters["rssdate"] = lambda ts: format_datetime(datetime.fromtimestamp(ts, timezone.utc))
    rss = env.from_string('''<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>ai:///feed</title><link>{{ site_url }}</link><description>Wiadomości AI dla programistów</description>{% for a in articles[:50] %}<item><title>{{ a.title_pl }}</title><link>{{ site_url }}/a/{{ a.slug }}</link><guid>{{ site_url }}/a/{{ a.slug }}</guid><description>{{ a.lead_pl }}</description><pubDate>{{ a.published_at|rssdate }}</pubDate></item>{% endfor %}</channel></rss>''').render(articles=articles, site_url=site_url.rstrip("/"))
    (output / "rss.xml").write_text(rss, encoding="utf-8")
    models = store.article_models()
    for article in articles:
        previous = store.article_by_id(article.delta_of) if article.delta_of is not None else None
        article_dir = articles_dir / article.slug
        article_dir.mkdir(parents=True, exist_ok=True)
        (article_dir / "index.html").write_text(render_article(article, previous, model=models.get(article.id)), encoding="utf-8")
    # Usuń sierotki: katalogi i płaskie *.html których już nie ma w bazie (nowa struktura = katalogi).
    live_slugs = {article.slug for article in articles}
    import shutil
    for d in articles_dir.iterdir():
        if d.is_dir() and d.name not in live_slugs:
            shutil.rmtree(d)
        elif d.suffix == ".html":
            d.unlink()  # stary płaski slug.html po migracji na katalogi
    for d in list(categories_dir.iterdir()) + list(pages_dir.iterdir()):
        if d.suffix == ".html":
            d.unlink()


def slugify(title):
    text = unicodedata.normalize("NFKD", title).translate(str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ"))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        raise ValueError("title has no usable slug characters")
    return text
