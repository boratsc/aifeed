"""One aggregation pass: fetch, classify, edit and render."""
import time
from pathlib import Path

import httpx

from . import editor, triage
from .fetch import ingest_feeds
from .render import write_site
from .sources import RSS_SOURCES, HTML_SOURCES, API_SOURCES
from .store import Store
from .tools import fetch_tool_versions


def run(settings, now=None):
    now = time.time() if now is None else now
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    last = store.db.execute("SELECT MAX(finished_at) FROM run_log").fetchone()[0]
    since = last if last is not None else now - settings.backfill_days * 86400
    with httpx.Client() as client:
        _, errors = ingest_feeds(client, store, {**RSS_SOURCES, **HTML_SOURCES, **API_SOURCES}, since)
        store.set_tool_versions(fetch_tool_versions(client))
    triage.run(settings, store)
    new_count = editor.run(settings, store)
    write_site(store, settings.out_dir, img_dir=settings.img_dir)
    if not errors:
        store.log_run(now, time.time(), new_count, [])
    return {"new": new_count, "errors": errors}
