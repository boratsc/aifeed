from aifeed.config import Settings
from aifeed.pipeline import run


def test_subsequent_pipeline_uses_last_success_not_backfill(tmp_path, monkeypatch):
    import aifeed.pipeline as pipeline
    from aifeed.store import Store
    store = Store(str(tmp_path / "db"))
    store.log_run(900000, 900100, 0, {})
    cutoff = []
    monkeypatch.setattr(pipeline, "Store", lambda path: store)
    monkeypatch.setattr(pipeline.httpx, "Client", lambda: FakeClient())
    monkeypatch.setattr(pipeline, "ingest_feeds", lambda client, store, sources, since, settings=None: cutoff.append(since) or (0, {}))
    monkeypatch.setattr(pipeline.triage, "run", lambda *args: 0)
    monkeypatch.setattr(pipeline.editor, "run", lambda *args: 0)
    monkeypatch.setattr(pipeline, "write_site", lambda *args, **kwargs: None)
    run(Settings(db_path=str(tmp_path / "db")), now=1000000)
    assert cutoff == [900100, 0]  # news (last watermark) + promocje (0 = bez filtra)


def test_failed_source_does_not_advance_global_watermark(tmp_path, monkeypatch):
    import aifeed.pipeline as pipeline
    from aifeed.store import Store
    store = Store(str(tmp_path / "db"))
    monkeypatch.setattr(pipeline, "Store", lambda path: store)
    monkeypatch.setattr(pipeline.httpx, "Client", lambda: FakeClient())
    monkeypatch.setattr(pipeline, "ingest_feeds", lambda *args: (0, {"broken": "timeout"}))
    monkeypatch.setattr(pipeline.triage, "run", lambda *args: 0)
    monkeypatch.setattr(pipeline.editor, "run", lambda *args: 0)
    monkeypatch.setattr(pipeline, "write_site", lambda *args, **kwargs: None)
    run(Settings(db_path=str(tmp_path / "db")), now=1000000)
    assert store.db.execute("SELECT MAX(finished_at) FROM run_log").fetchone()[0] is None


def test_pipeline_orders_fetch_triage_edit_render(tmp_path, monkeypatch):
    import aifeed.pipeline as pipeline
    from aifeed.store import Store
    calls = []
    monkeypatch.setattr(pipeline, "Store", Store)
    monkeypatch.setattr(pipeline.httpx, "Client", lambda: FakeClient())
    monkeypatch.setattr(pipeline, "ingest_feeds", lambda *args: calls.append("fetch") or (2, {}))
    monkeypatch.setattr(pipeline.triage, "run", lambda *args: calls.append("triage") or 2)
    monkeypatch.setattr(pipeline.editor, "run", lambda *args: calls.append("edit") or 1)
    monkeypatch.setattr(pipeline, "write_site", lambda *args, **kwargs: calls.append("render"))
    result = run(Settings(db_path=str(tmp_path / "db")), now=1000000)
    assert calls == ["fetch", "fetch", "triage", "edit", "render"]
    assert result["new"] == 1 and result["errors"] == {}


class FakeClient:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def test_pipeline_ingests_promos_with_old_cutoff_not_news_cutoff(tmp_path, monkeypatch):
    import aifeed.pipeline as pipeline
    from aifeed.store import Store
    store = Store(str(tmp_path / "db"))
    store.log_run(900000, 900100, 0, {})
    seen = []
    monkeypatch.setattr(pipeline, "Store", lambda path: store)
    monkeypatch.setattr(pipeline.httpx, "Client", lambda: FakeClient())
    def fake_ingest(client, store, sources, since, settings=None):
        seen.append((set(sources), since))
        return (0, {})
    monkeypatch.setattr(pipeline, "ingest_feeds", fake_ingest)
    monkeypatch.setattr(pipeline.triage, "run", lambda *args: 0)
    monkeypatch.setattr(pipeline.editor, "run", lambda *args: 0)
    monkeypatch.setattr(pipeline, "write_site", lambda *args, **kwargs: None)
    run(Settings(db_path=str(tmp_path / "db")), now=1000000)
    # dwa wywołania: news (since=900100) + promocje (since=0 = bez filtra)
    assert len(seen) == 2
    news_call, promo_call = seen[0], seen[1]
    assert "aitkradar_deals" not in news_call[0]
    assert promo_call[1] == 0  # promocje nie filtrowane po dacie


def test_main_retries_backlog_until_empty(tmp_path, monkeypatch):
    import aifeed.__main__ as entry
    calls = {"n": 0}
    sleeps = []
    def fake_run(settings, now=None):
        calls["n"] += 1
        # pierwsza próba: padła sieć, zostały zaległości; druga: pusto
        if calls["n"] == 1:
            return {"new": 0, "errors": {}, "backlog": 5}
        return {"new": 5, "errors": {}, "backlog": 0}
    monkeypatch.setattr(entry, "run", fake_run)
    monkeypatch.setattr(entry.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(entry, "load_env", lambda p: None)
    entry.main(retry_interval=1800)
    assert calls["n"] == 2
    assert sleeps == [1800]


def test_main_stops_after_max_retries(tmp_path, monkeypatch):
    import aifeed.__main__ as entry
    calls = {"n": 0}
    def fake_run(settings, now=None):
        calls["n"] += 1
        return {"new": 0, "errors": {}, "backlog": 3}
    monkeypatch.setattr(entry, "run", fake_run)
    monkeypatch.setattr(entry.time, "sleep", lambda s: None)
    monkeypatch.setattr(entry, "load_env", lambda p: None)
    entry.main(retry_interval=1800, max_retries=2)
    assert calls["n"] == 3  # pierwsza próba + 2 retry


def test_run_reports_backlog(tmp_path, monkeypatch):
    import aifeed.pipeline as pipeline
    from aifeed.store import Store
    store = Store(str(tmp_path / "db"))
    rid = store.upsert_raw("s", "https://x/1", "T", 1000.0, "C", None)
    store.db.execute("UPDATE raw SET triaged=1, relevant=1")
    store.db.commit()
    monkeypatch.setattr(pipeline, "Store", lambda path: store)
    monkeypatch.setattr(pipeline.httpx, "Client", lambda: FakeClient())
    monkeypatch.setattr(pipeline, "ingest_feeds", lambda *a, **k: (0, {}))
    monkeypatch.setattr(pipeline, "fetch_tool_versions", lambda c: {})
    monkeypatch.setattr(pipeline.triage, "run", lambda *a: 0)
    monkeypatch.setattr(pipeline.editor, "run", lambda *a: 0)
    monkeypatch.setattr(pipeline, "write_site", lambda *a, **k: None)
    result = pipeline.run(Settings(db_path=str(tmp_path / "db")), now=1000000)
    assert result["backlog"] == 1
