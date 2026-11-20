import pytest

from aifeed.editor import validate_article, edit_article, group_materials, save_new_article
from aifeed.store import Store


def test_invalid_article_does_not_block_other_groups(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    ids = [store.upsert_raw("s", f"https://example.org/{i}", f"T{i}", 1000.0+i, "F", None) for i in range(2)]
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    monkeypatch.setattr(editor, "group_materials", lambda *args: [[ids[0]], [ids[1]]])
    def edit(settings, materials, sources):
        if materials[0]["id"] == ids[0]:
            raise ValueError("article exceeds 4000 words")
        return {"title_pl": "Dobry", "lead_pl": "L", "body_pl": "B", "category": "modele", "sources": sources}
    monkeypatch.setattr(editor, "edit_article", edit)
    monkeypatch.setattr(editor, "classify_candidate", lambda *args: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    assert editor.run(Settings(), store) == 1
    assert store.article_by_slug("dobry") is not None
    assert [item["id"] for item in store.relevant_candidates()] == [ids[0]]


def test_merge_updates_existing_article_sources(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    aid = store.add_article("a", "A", "L", "B", "modele", [{"name": "A", "url": "https://a.example"}], None, None, [1.0, 0.0], 900.0, None)
    raw_id = store.upsert_raw("s", "https://b.example/2", "Nowe", 1000.0, "Facts", None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='Facts' WHERE id=?", (raw_id,))
    store.db.commit()
    article = {"title_pl": "A", "lead_pl": "L", "body_pl": "Uzupełnione", "category": "modele"}
    monkeypatch.setattr(editor, "group_materials", lambda *args: [[raw_id]])
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**article, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *args: {"verdict": "merge", "article_id": aid, "embedding": [1.0, 0.0]})
    assert editor.run(Settings(), store) == 0
    assert len(store.article_by_slug("a").sources) == 2
    assert store.article_by_slug("a").body_pl == "Uzupełnione"


def test_extension_creates_delta_article_with_backlink(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    parent = store.add_article("original", "Pierwszy", "Lead", "Body", "modele", [], None, None, [1.0, 0.0], 900.0, None)
    raw_id = store.upsert_raw("s", "https://example.org/2", "Nowe", 1000.0, "Nowe fakty", None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='Nowe fakty' WHERE id=?", (raw_id,))
    store.db.commit()
    article = {"title_pl": "Nowe wyniki", "lead_pl": "Lead", "body_pl": "Tylko nowości", "category": "modele"}
    monkeypatch.setattr(editor, "group_materials", lambda *args: [[raw_id]])
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**article, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *args: {"verdict": "extension", "article_id": parent, "embedding": [0.9, 0.1]})
    assert editor.run(Settings(), store) == 1
    assert store.article_by_slug("nowe-wyniki").delta_of == parent


def test_run_creates_one_article_for_group(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    raw_id = store.upsert_raw("source", "https://example.org/1", "T", 1000.0, "Facts", None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='Podsumowanie' WHERE id=?", (raw_id,))
    store.db.commit()
    output = {"title_pl": "Artykuł", "lead_pl": "Lead", "body_pl": "Treść", "category": "modele"}
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda settings, store, candidate: {"verdict": "new", "article_id": None, "embedding": [1.0, 0.0]})
    assert editor.run(Settings(), store) == 1
    assert store.article_by_slug("artykul").sources[0]["url"] == "https://example.org/1"
    assert store.relevant_candidates() == []


def test_save_new_article_creates_unique_slugs(tmp_path):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": "B", "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    first = save_new_article(store, article, [1.0, 0.0], 1000.0)
    second = save_new_article(store, article, [1.0, 0.0], 1000.0)
    assert store.article_by_slug("t").id == first
    assert store.article_by_slug("t-2").id == second
from aifeed.config import Settings


def test_group_materials_sanitizes_missing_and_foreign_ids(monkeypatch):
    import aifeed.editor as editor
    materials = [{"id": 1}, {"id": 2}]
    # missing ID -> singleton
    monkeypatch.setattr(editor, "chat", lambda *args: [[1]])
    assert group_materials(Settings(), materials) == [[1], [2]]
    # foreign ID dropped, missing becomes singleton
    monkeypatch.setattr(editor, "chat", lambda *args: [[1, 99]])
    assert group_materials(Settings(), materials) == [[1], [2]]
    # repeated ID deduplicated within group
    monkeypatch.setattr(editor, "chat", lambda *args: [[1, 1, 2]])
    assert group_materials(Settings(), materials) == [[1, 2]]


def test_group_materials_keeps_complete_partition(monkeypatch):
    import aifeed.editor as editor
    monkeypatch.setattr(editor, "chat", lambda *args: [[1, 2]])
    assert group_materials(Settings(), [{"id": 1}, {"id": 2}]) == [[1, 2]]


def test_edit_article_keeps_all_input_sources(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "A", "url": "https://a.example/1"}, {"name": "B", "url": "https://b.example/2"}]
    output = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nB'
    monkeypatch.setattr(editor, "chat", lambda *args, **kw: output)
    result = edit_article(Settings(), [{"title": "News", "content": "Facts", "url": sources[1]["url"], "source_id": "B"}], sources)
    assert result["sources"] == sources
    assert "—" not in result["title_pl"]


def test_overlong_article_is_shortened_by_editor(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "S", "url": "https://example.org"}]
    long = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + " ".join(["słowo"] * 4001)
    short = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nKrótka treść.'
    responses = iter([long, short])
    calls = []
    def fake_chat(settings, model, system, user, **kw):
        calls.append(user)
        return next(responses)
    monkeypatch.setattr(editor, "chat", fake_chat)
    result = editor.edit_article(Settings(), [{"title": "T"}], sources)
    assert result["body_pl"] == "Krótka treść."
    assert len(calls) == 2
    assert "4000" in calls[1]


def test_validate_article_rejects_overlong_body():
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": " ".join(["słowo"] * 4001),
               "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    with pytest.raises(ValueError):
        validate_article(article)


def test_validate_article_accepts_long_faithful_translation():
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": " ".join(["słowo"] * 1500),
               "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    assert validate_article(article) == article


def test_editor_prompt_demands_faithful_translation(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured["system"] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nB'
    monkeypatch.setattr(editor, "chat", fake_chat)
    editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert "Przetłumacz" in captured["system"]
    assert "nie streszczaj" in captured["system"]


def test_validate_article_requires_source_and_text():
    article = {"title_pl": "Tytuł", "lead_pl": "Lead.", "body_pl": "Treść.",
               "category": "modele", "sources": [{"name": "Źródło", "url": "https://example.org/1"}]}
    assert validate_article(article) == article


def test_validate_article_rejects_unsafe_sources_and_empty_text():
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": "B", "category": "modele",
               "sources": [{"name": "Źródło", "url": "javascript:alert(1)"}]}
    with pytest.raises(ValueError):
        validate_article(article)
    article["sources"] = []
    with pytest.raises(ValueError):
        validate_article(article)


def test_validate_article_rejects_meta_commentary():
    base = {"title_pl": "T", "lead_pl": "L", "category": "modele",
            "sources": [{"name": "S", "url": "https://example.org"}]}
    for meta in ("Temat został zakwalifikowany jako narzędzie.",
                 "Artykuł opiera się wyłącznie na opisie producenta.",
                 "W materiale nie zamieszczono danych o cenach.",
                 "Materiał nie zawiera danych o testach.",
                 "Opublikowany opis koncentruje się na charakterystyce."):
        with pytest.raises(ValueError, match="meta"):
            validate_article({**base, "body_pl": "Fakt o produkcie. " + meta})


def test_validate_article_keeps_product_fact_about_missing_data():
    article = {"title_pl": "T", "lead_pl": "L",
               "body_pl": "Google nie podało ceny ani daty premiery. Funkcja jest dostępna w preview.",
               "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    assert validate_article(article) == article


def test_editor_prompt_forbids_narrating_compliance(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured["system"] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nB'
    monkeypatch.setattr(editor, "chat", fake_chat)
    editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert "Nie wspominaj o ich braku" in captured["system"]
    assert "Nie opisuj procesu redakcji" in captured["system"]


def _one_candidate(tmp_path):
    store = Store(str(tmp_path / "db"))
    raw_id = store.upsert_raw("s", "https://example.org/1", "T", 1000.0, "F", None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    return store, raw_id


def test_group_key_is_order_insensitive():
    import aifeed.editor as editor
    assert editor.group_key([2, 1]) == editor.group_key([1, 2])
    assert editor.group_key([1, 2]) != editor.group_key([1])


def test_done_group_is_skipped_and_recovered(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    key = editor.group_key([raw_id])
    store.mark_done(key, 1)
    def boom(*a):
        raise AssertionError("edit_article called for done group")
    monkeypatch.setattr(editor, "edit_article", boom)
    assert editor.run(Settings(), store) == 0
    assert store.relevant_candidates() == []


def test_fresh_running_group_is_skipped(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    store.mark_running(editor.group_key([raw_id]))
    def boom(*a):
        raise AssertionError("edit_article called for fresh running group")
    monkeypatch.setattr(editor, "edit_article", boom)
    assert editor.run(Settings(), store) == 0
    assert [item["id"] for item in store.relevant_candidates()] == [raw_id]


def test_stale_running_group_is_retried(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    key = editor.group_key([raw_id])
    store.mark_running(key)
    store.db.execute("UPDATE editor_job SET started_at = started_at - 99999, updated_at = updated_at - 99999")
    store.db.commit()
    output = {"title_pl": "Artykuł", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    assert editor.run(Settings(), store) == 1
    assert store.job(key)["state"] == "done"


def test_failed_group_is_retried_next_run(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    key = editor.group_key([raw_id])
    def bad(settings, materials, sources):
        raise ValueError("article exceeds 4000 words")
    monkeypatch.setattr(editor, "edit_article", bad)
    assert editor.run(Settings(), store) == 0
    assert store.job(key)["state"] == "failed"
    assert [item["id"] for item in store.relevant_candidates()] == [raw_id]
    output = {"title_pl": "Artykuł", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    assert editor.run(Settings(), store) == 1
    assert store.job(key)["state"] == "done"


def test_same_verdict_marks_processed(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    aid = store.add_article("a", "A", "L", "B", "modele", [{"name": "S", "url": "https://example.org"}], None, None, [1.0], 900.0, None)
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "same", "article_id": aid, "embedding": [1.0]})
    assert editor.run(Settings(), store) == 0
    assert store.relevant_candidates() == []
    assert store.job(editor.group_key([raw_id]))["state"] == "done"


def test_run_uses_configured_concurrency(tmp_path, monkeypatch):
    import aifeed.editor as editor
    from concurrent.futures import ThreadPoolExecutor as RealExecutor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    captured = {}
    class CapturingExecutor(RealExecutor):
        def __init__(self, *args, **kwargs):
            captured["max_workers"] = kwargs.get("max_workers")
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(editor, "ThreadPoolExecutor", CapturingExecutor)
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    settings = Settings()
    assert editor.run(settings, store) == 1
    assert captured["max_workers"] == settings.editor_concurrency


def test_worker_exception_marks_failed_and_does_not_abort_run(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    def boom(*a):
        raise RuntimeError("embed timeout")
    monkeypatch.setattr(editor, "classify_candidate", boom)
    assert editor.run(Settings(), store) == 0
    assert store.job(editor.group_key([raw_id]))["state"] == "failed"
    assert [item["id"] for item in store.relevant_candidates()] == [raw_id]


def test_worker_heartbeat_prevents_stale_reclaim(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    key = editor.group_key([raw_id])
    store.mark_running(key)
    store.db.execute("UPDATE editor_job SET started_at = started_at - 99999, updated_at = updated_at - 99999")
    store.db.commit()
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    def slow_edit(settings, materials, sources):
        # praca trwa; heartbeat w trakcie ma odświeżyć updated_at (własne połączenie: test chodzi w workerze)
        row = Store(store.db_path).job(key)
        assert row["updated_at"] > row["started_at"]
        return {**output, "sources": sources}
    monkeypatch.setattr(editor, "edit_article", slow_edit)
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    monkeypatch.setattr(editor, "HEARTBEAT_SECONDS", 0)
    assert editor.run(Settings(), store) == 1


def test_duplicate_titles_across_workers_do_not_crash(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    ids = [store.upsert_raw("s", f"https://example.org/{i}", f"T{i}", 1000.0+i, "F", None) for i in range(2)]
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[ids[0]], [ids[1]]])
    output = {"title_pl": "Ten sam", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    # obaj workerzy widzą pustą bazę embeddingów (snapshot przed zapisem) → obaj new
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    assert editor.run(Settings(), store) == 2
    assert store.article_by_slug("ten-sam") is not None
    assert store.article_by_slug("ten-sam-2") is not None


def test_parallel_workers_write_to_one_db(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    ids = [store.upsert_raw("s", f"https://example.org/{i}", f"T{i}", 1000.0+i, "F", None) for i in range(6)]
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[i] for i in ids])
    def edit(settings, materials, sources):
        return {"title_pl": f"Art {materials[0]['id']}", "lead_pl": "L", "body_pl": "B", "category": "modele", "sources": sources}
    monkeypatch.setattr(editor, "edit_article", edit)
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    assert editor.run(Settings(), store) == 6
    assert store.relevant_candidates() == []
    states = [r["state"] for r in store.db.execute("SELECT state FROM editor_job")]
    assert states == ["done"] * 6


def test_editor_prompt_preserves_figure_markdown(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured["system"] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nB'
    monkeypatch.setattr(editor, "chat", fake_chat)
    editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert "Obrazy" in captured["system"]
    assert "![](url)" in captured["system"]
    assert "nie pomijaj" in captured["system"]


def test_parse_article_splits_header_json_and_raw_body():
    from aifeed.editor import parse_article
    raw = '{"title_pl": "T", "lead_pl": "L", "category": "modele", "bench": ["B"], "promo": false}\n\n---BODY---\n\n## Sekcja\nAkapit z "cudzysłowem" i ![wykres](https://x/y.png)\n'
    a = parse_article(raw)
    assert a["title_pl"] == "T"
    assert a["category"] == "modele"
    assert a["bench"] == ["B"]
    assert a["body_pl"] == '## Sekcja\nAkapit z "cudzysłowem" i ![wykres](https://x/y.png)'


def test_parse_article_body_never_needs_json_escaping():
    from aifeed.editor import parse_article
    body = '\n'.join(f'## Wiersz {i}\nTekst z "cudzysłowem", [link](https://x/{i}) i \nraw newline' for i in range(100))
    raw = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + body
    a = parse_article(raw)
    assert a["body_pl"] == body


def test_parse_article_falls_back_to_full_json():
    from aifeed.editor import parse_article
    raw = '{"title_pl":"T","lead_pl":"L","body_pl":"B","category":"modele","bench":null,"promo":false}'
    assert parse_article(raw)["body_pl"] == "B"


def test_editor_prompt_uses_body_delimiter_not_json_body(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured["system"] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nB'
    monkeypatch.setattr(editor, "chat", fake_chat)
    a = editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert a["body_pl"] == "B"
    assert "---BODY---" in captured["system"]
    assert "JSON" in captured["system"]
