import math
import time

import pytest

from aifeed.store import Store, cosine


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "test.db"))


def test_upsert_raw_idempotent_by_url(store):
    id1 = store.upsert_raw("openai", "https://x.com/a", "Title", 1000.0, "body", None)
    id2 = store.upsert_raw("openai", "https://x.com/a", "Title changed", 1000.0, "body2", None)
    assert id1 == id2
    rows = store.untriaged()
    assert len(rows) == 1


def test_untriaged_and_mark_triaged(store):
    rid = store.upsert_raw("s", "https://x.com/1", "T", 1.0, "c", None)
    assert [r.id for r in store.untriaged()] == [rid]
    store.mark_triaged(rid, relevant=True, category="modele")
    assert store.untriaged() == []


def test_relevant_candidates_keep_triage_summary(store):
    rid = store.upsert_raw("s", "https://x.com/1", "T", 1.0, "c", None)
    store.db.execute("UPDATE raw SET triaged = 1, relevant = 1, category = ?, summary_pl = ? WHERE id = ?", ("modele", "Podsumowanie.", rid))
    store.db.commit()
    candidates = store.relevant_candidates()
    assert candidates[0]["id"] == rid
    assert candidates[0]["summary_pl"] == "Podsumowanie."
    store.mark_processed(rid)
    assert store.relevant_candidates() == []


def test_article_roundtrip_with_json_fields(store):
    sources = [{"name": "z.ai", "url": "https://z.ai/news", "note": "release"}]
    bench = [{"name": "SWE-bench", "value": "71,4%", "note": "vs K3.1"}]
    aid = store.add_article(
        slug="glm-5-4", title_pl="GLM-5.4", lead_pl="lead", body_pl="body",
        category="modele", sources=sources, bench=bench, promo="do 26.11",
        embedding=[0.1, 0.2], published_at=2000.0, delta_of=None,
    )
    art = store.article_by_slug("glm-5-4")
    assert art.id == aid
    assert art.sources == sources
    assert art.bench == bench
    assert art.promo == "do 26.11"
    assert art.embedding == [0.1, 0.2]
    assert art.delta_of is None


def test_articles_since_filters_by_time(store):
    store.add_article("old", "Stary", "l", "b", "modele", [], None, None, [0.0], 1000.0, None)
    store.add_article("new", "Nowy", "l", "b", "modele", [], None, None, [0.0], 2000.0, None)
    recent = store.articles_since(1500.0)
    assert [a.slug for a in recent] == ["new"]


def test_delta_of_stored_and_read(store):
    parent = store.add_article("parent", "P", "l", "b", "modele", [], None, None, [0.0], 1000.0, None)
    child = store.add_article("child", "C", "l", "b", "modele", [], None, None, [0.0], 2000.0, parent)
    assert store.article_by_slug("child").delta_of == parent


def test_set_price_first_is_baseline_no_change(store):
    store.set_price("z.ai", "glm-5.4", 2.20, 0.60, "USD", 1000.0)
    assert store.price_baseline("z.ai", "glm-5.4")["price_in"] == 2.20
    assert store.price_changes() == []


def test_set_price_change_recorded(store):
    store.set_price("z.ai", "glm-5.4", 2.20, 0.60, "USD", 1000.0)
    store.set_price("z.ai", "glm-5.4", 1.32, 0.60, "USD", 2000.0)
    changes = store.price_changes()
    assert len(changes) == 1
    assert changes[0]["old_in"] == 2.20
    assert changes[0]["new_in"] == 1.32
    assert store.price_baseline("z.ai", "glm-5.4")["price_in"] == 1.32


def test_all_embeddings_window(store):
    now = time.time()
    store.add_article("a", "A", "l", "b", "modele", [], None, None, [1.0, 0.0], now - 86400, None)
    store.add_article("b", "B", "l", "b", "modele", [], None, None, [0.0, 1.0], now - 30 * 86400, None)
    embs = store.all_embeddings(days=14)
    assert [e[0] for e in embs] == [store.article_by_slug("a").id]


def test_update_article(store):
    aid = store.add_article("a", "A", "l", "b", "modele", [{"name": "x", "url": "https://x.com"}], None, None, [0.0], 1000.0, None)
    store.update_article(aid, sources=[{"name": "x", "url": "https://x.com"}, {"name": "y", "url": "https://y.com"}], body_pl="new body")
    art = store.article_by_slug("a")
    assert len(art.sources) == 2
    assert art.body_pl == "new body"
    assert art.updated_at >= art.published_at


def test_cosine_rejects_mismatched_embedding_dimensions():
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0])


def test_cosine():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0

def test_editor_job_lifecycle(tmp_path):
    store = Store(str(tmp_path / "db"))
    assert store.db_path == str(tmp_path / "db")
    assert store.job("k1") is None
    store.mark_running("k1")
    assert store.job("k1")["state"] == "running"
    assert store.job("k1")["started_at"] > 0
    store.mark_done("k1", 7)
    assert store.job("k1")["state"] == "done"
    assert store.job("k1")["article_id"] == 7
    store.mark_failed("k1")
    assert store.job("k1")["state"] == "failed"
    # mark_running resetuje article_id i started_at
    store.mark_running("k1")
    assert store.job("k1")["article_id"] is None


def test_mark_done_creates_row_when_missing(tmp_path):
    # Recovery z DB sprzed istnienia tabeli: mark_done bez wcześniejszego mark_running.
    store = Store(str(tmp_path / "db"))
    store.mark_done("k9", 3)
    assert store.job("k9")["state"] == "done"
    assert store.job("k9")["article_id"] == 3
    store.mark_failed("k8")
    assert store.job("k8")["state"] == "failed"


def test_settings_editor_defaults(monkeypatch):
    from aifeed.config import Settings
    for var in ("EDITOR_CONCURRENCY", "EDITOR_429_RETRIES", "EDITOR_STALE_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert (s.editor_concurrency, s.editor_429_retries, s.editor_stale_seconds) == (4, 4, 1800)
    monkeypatch.setenv("EDITOR_CONCURRENCY", "2")
    assert Settings.from_env().editor_concurrency == 2


def test_research_engines_lifecycle_and_lookup(tmp_path):
    store = Store(str(tmp_path / "db"))
    store.add_research_engines(3, ["Firecrawl", "Sonar"])
    assert store.research_engines()[3] == ["Firecrawl", "Sonar"]
    store.add_research_engines(3, ["Firecrawl", "Sonar"])
    assert store.research_engines()[3] == ["Firecrawl", "Sonar"]


def test_existing_database_gains_research_engines(tmp_path):
    store = Store(str(tmp_path / "db"))
    aid = store.add_article("a", "T", "L", "B", "modele", [], None, None, [], 1.0, None)
    store.db.execute("DROP TABLE research_engines")
    store.db.commit()
    reopened = Store(str(tmp_path / "db"))
    reopened.add_research_engines(aid, ["Firecrawl"])
    assert reopened.research_engines()[aid] == ["Firecrawl"]
