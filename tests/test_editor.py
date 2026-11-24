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
            raise ValueError("article exceeds 6000 words")
        return {"title_pl": "Dobry", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele", "sources": sources}
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
    article = {"title_pl": "Nowe wyniki", "lead_pl": "Lead", "body_pl": "Tylko nowosci i fakty z kolejnych badan.", "category": "modele"}
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
    output = {"title_pl": "Artykuł", "lead_pl": "Lead", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda settings, store, candidate: {"verdict": "new", "article_id": None, "embedding": [1.0, 0.0]})
    assert editor.run(Settings(), store) == 1
    assert store.article_by_slug("artykul").sources[0]["url"] == "https://example.org/1"
    assert store.relevant_candidates() == []


def test_save_new_article_creates_unique_slugs(tmp_path):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
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
    output = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
    monkeypatch.setattr(editor, "chat", lambda *args, **kw: output)
    result = edit_article(Settings(), [{"title": "News", "content": "Facts", "url": sources[1]["url"], "source_id": "B"}], sources)
    assert result["sources"] == sources
    assert "—" not in result["title_pl"]


def test_overlong_article_is_shortened_by_editor(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "S", "url": "https://example.org"}]
    long = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + " ".join(["słowo"] * 6001) + "."
    short = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nKrotka ale prawdziwa tresc artykulu.'
    responses = iter([long, short])
    calls = []
    def fake_chat(settings, model, system, user, **kw):
        calls.append(user)
        return next(responses)
    monkeypatch.setattr(editor, "chat", fake_chat)
    result = editor.edit_article(Settings(), [{"title": "T"}], sources)
    assert result["body_pl"] == "Krotka ale prawdziwa tresc artykulu."
    assert len(calls) == 2
    assert "5000" in calls[1]


def test_validate_article_rejects_overlong_body():
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": " ".join(["słowo"] * 6001),
               "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    with pytest.raises(ValueError):
        validate_article(article)


def test_validate_article_accepts_long_faithful_translation():
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": " ".join(["słowo"] * 1500) + ".",
               "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    assert validate_article(article) == article


def test_editor_prompt_demands_faithful_translation(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured["system"] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
    monkeypatch.setattr(editor, "chat", fake_chat)
    editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert "Przetłumacz" in captured["system"]
    assert "nie streszczaj" in captured["system"]


def test_validate_article_requires_source_and_text():
    article = {"title_pl": "Tytuł", "lead_pl": "Lead.", "body_pl": "Prawdziwa tresc artykulu o faktach.",
               "category": "modele", "sources": [{"name": "Źródło", "url": "https://example.org/1"}]}
    assert validate_article(article) == article


def test_validate_article_rejects_unsafe_sources_and_empty_text():
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele",
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
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
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
    output = {"title_pl": "Artykuł", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
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
        raise ValueError("article exceeds 6000 words")
    monkeypatch.setattr(editor, "edit_article", bad)
    assert editor.run(Settings(), store) == 0
    assert store.job(key)["state"] == "failed"
    assert [item["id"] for item in store.relevant_candidates()] == [raw_id]
    output = {"title_pl": "Artykuł", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    assert editor.run(Settings(), store) == 1
    assert store.job(key)["state"] == "done"


def test_same_verdict_marks_processed(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    aid = store.add_article("a", "A", "L", "B", "modele", [{"name": "S", "url": "https://example.org"}], None, None, [1.0], 900.0, None)
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
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
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
    monkeypatch.setattr(editor, "edit_article", lambda settings, materials, sources: {**output, "sources": sources})
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    settings = Settings()
    assert editor.run(settings, store) == 1
    assert captured["max_workers"] == settings.editor_concurrency


def test_worker_exception_marks_failed_and_does_not_abort_run(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store, raw_id = _one_candidate(tmp_path)
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
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
    output = {"title_pl": "A", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
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
    output = {"title_pl": "Ten sam", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele"}
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
        return {"title_pl": f"Art {materials[0]['id']}", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele", "sources": sources}
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
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
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
    raw = '{"title_pl":"T","lead_pl":"L","body_pl":"Prawdziwa tresc artykulu o faktach.","category":"modele","bench":null,"promo":false}'
    assert parse_article(raw)["body_pl"] == "Prawdziwa tresc artykulu o faktach."


def test_editor_prompt_uses_body_delimiter_not_json_body(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured["system"] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
    monkeypatch.setattr(editor, "chat", fake_chat)
    a = editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert a["body_pl"] == "Prawdziwa tresc artykulu o faktach."
    assert "---BODY---" in captured["system"]
    assert "JSON" in captured["system"]


def test_validate_article_rejects_cjk_in_any_field():
    for field in ("title_pl", "lead_pl", "body_pl"):
        a = {"title_pl": "T", "lead_pl": "L", "body_pl": "Prawdziwa tresc artykulu o faktach.", "category": "modele",
             "sources": [{"name": "S", "url": "https://example.org"}]}
        a[field] = "Prawdziwa tresc artykulu 阿里云 z faktami."
        with pytest.raises(ValueError, match="CJK"):
            validate_article(a)


def test_editor_prompt_forbids_cjk_and_demands_transliteration(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured["system"] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
    monkeypatch.setattr(editor, "chat", fake_chat)
    editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert "chińsk" in captured["system"].lower() or "cjk" in captured["system"].lower()
    assert "阿里云" in captured["system"] or "transkryb" in captured["system"].lower()


def test_edit_article_retries_when_cjk_leaks_to_body(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "S", "url": "https://example.org"}]
    bad = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPromocja 阿里云 na modele dla firm.'
    good = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPromocja Alibaba Cloud na modele dla firm.'
    responses = iter([bad, good])
    calls = []
    def fake_chat(settings, model, system, user, **kw):
        calls.append(user)
        return next(responses)
    monkeypatch.setattr(editor, "chat", fake_chat)
    result = editor.edit_article(Settings(), [{"title": "N"}], sources)
    assert result["body_pl"] == "Promocja Alibaba Cloud na modele dla firm."
    assert len(calls) == 2
    assert "阿里云" in calls[1]


def test_parse_article_tolerates_trailing_content_after_json():
    from aifeed.editor import parse_article
    raw = '{"title_pl":"T","lead_pl":"L","body_pl":"Prawdziwa tresc artykulu o faktach.","category":"modele","bench":null,"promo":false}\n\njakiś śmieć za JSON'
    a = parse_article(raw)
    assert a["title_pl"] == "T"
    assert a["body_pl"] == "Prawdziwa tresc artykulu o faktach."


def test_parse_article_delimiter_with_trailing_junk_in_header():
    from aifeed.editor import parse_article
    raw = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false} extra\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
    a = parse_article(raw)
    assert a["title_pl"] == "T"
    assert a["body_pl"] == "Prawdziwa tresc artykulu o faktach."


def test_validate_article_rejects_near_empty_body():
    for body in ("##", " ", "T"):
        article = {"title_pl": "T", "lead_pl": "L", "body_pl": body,
                   "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
        with pytest.raises(ValueError):
            validate_article(article)


def test_short_body_relative_to_source_triggers_fallback_model(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "S", "url": "https://example.org"}]
    long_source = [{"title": "T", "content": "fakt " * 400}]  # ~2000 znaków źródła
    stub = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nKrotkie streszczenie zamiast tlumaczenia.'
    full = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + "Pelne wierne tlumaczenie z faktami. " * 40
    calls = []
    def fake_chat(settings, model, system, user, **kw):
        calls.append(model)
        return stub if model == "redaktor" else full
    monkeypatch.setattr(editor, "chat", fake_chat)
    s = Settings(editor_model="redaktor", editor_fallback_model="drugi-redaktor")
    result = editor.edit_article(s, long_source, sources)
    assert calls == ["redaktor", "drugi-redaktor"]
    assert len(result["body_pl"].split()) > 100


def test_good_body_does_not_trigger_fallback(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "S", "url": "https://example.org"}]
    full = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + "Pelne wierne tlumaczenie z faktami. " * 40
    calls = []
    def fake_chat(settings, model, system, user, **kw):
        calls.append(model)
        return full
    monkeypatch.setattr(editor, "chat", fake_chat)
    s = Settings(editor_model="redaktor", editor_fallback_model="drugi-redaktor")
    editor.edit_article(s, [{"title": "T", "content": "fakt " * 400}], sources)
    assert calls == ["redaktor"]


def test_short_source_short_body_is_fine(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "S", "url": "https://example.org"}]
    short = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nWydano wersje 1.2.3 z poprawkami bledow.'
    calls = []
    def fake_chat(settings, model, system, user, **kw):
        calls.append(model)
        return short
    monkeypatch.setattr(editor, "chat", fake_chat)
    s = Settings(editor_model="redaktor", editor_fallback_model="drugi-redaktor")
    editor.edit_article(s, [{"title": "T", "content": "Wydano 1.2.3."}], sources)
    assert calls == ["redaktor"]  # krótkie źródło = krótki artykuł OK


def test_escalation_updates_job_model(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    raw_id = store.upsert_raw("s", "https://example.org/1", "T", 1000.0, "fakt " * 400, None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    monkeypatch.setattr(editor, "group_materials", lambda settings, materials: [[raw_id]])
    stub = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nKrotkie streszczenie zamiast tlumaczenia.'
    full = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + "Pelne wierne tlumaczenie z faktami. " * 40
    models_seen = []
    def fake_chat(settings, model, system, user, **kw):
        models_seen.append(model)
        editor._state_model = model  # symuluj last_model przez thread-local llm
        import aifeed.llm as llm
        llm._state.model = model
        return stub if model == "redaktor" else full
    monkeypatch.setattr(editor, "chat", fake_chat)
    monkeypatch.setattr(editor, "classify_candidate", lambda *a: {"verdict": "new", "article_id": None, "embedding": [1.0]})
    s = Settings(db_path=str(tmp_path / "db"), editor_model="redaktor", editor_fallback_model="drugi-redaktor")
    assert editor.run(s, store) == 1
    aid = store.db.execute("SELECT id FROM article").fetchone()["id"]
    assert store.article_models()[aid] == "drugi-redaktor"


def test_validate_article_rejects_mid_sentence_truncation():
    article = {"title_pl": "T", "lead_pl": "L",
               "body_pl": "Prawdziwa tresc artykulu o faktach i liczbach. Wiersz cennika stanowi jedyn",
               "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    with pytest.raises(ValueError, match="truncated"):
        validate_article(article)


def test_validate_article_accepts_complete_ending():
    for ending in ("Koniec zdania.", "Koniec zdania!", "Pytanie?", "## Naglowek", "* 0,40 USD za milion tokenow",
                   "Tagi: datasette, projekty", "`select json_object('a', 1)`", "Szczegoly: https://example.org/promo",
                   "Zrodlo: Improved GUI testing · GIMP", "Vipul Ved Prakash\nCEO i wspolzalozyciel, Together AI", "```bash\nuv pip install localcode\n```"):
        article = {"title_pl": "T", "lead_pl": "L",
                   "body_pl": "Prawdziwa tresc artykulu o faktach i liczbach.\n" + ending,
                   "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
        assert validate_article(article) == article


def test_validate_article_rejects_unclosed_code_fence():
    for last_cmd in ("localcode", "```bash"):
        article = {"title_pl": "T", "lead_pl": "L",
                   "body_pl": "Prawdziwa tresc artykulu o faktach.\n\n```bash\nuv pip install localcode\n" + last_cmd,
                   "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
        with pytest.raises(ValueError, match="truncated"):
            validate_article(article)


def test_edit_article_prompt_marks_research_as_merge_material(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured['system'] = system
        captured['user'] = user
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach i liczbach.'
    monkeypatch.setattr(editor, 'chat', fake_chat)
    materials = [
        {'title': 'News', 'content': 'fakt ' * 300},
        {'title': 'Research: model card', 'content': 'official 80 tokens/s decode benchmark ' * 40, 'research': True},
    ]
    editor.edit_article(Settings(), materials, [{'name': 'S', 'url': 'https://example.org'}])
    assert 'research' in captured['system'].lower()
    assert 'Research: model card' in captured['user']


def test_group_gets_research_materials_when_source_is_thin(tmp_path, monkeypatch):
    import aifeed.editor as editor
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    raw_id = store.upsert_raw('s', 'https://example.org/1', 'Thin news', 1000.0, 'krotka zajawka', None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    captured = {}
    monkeypatch.setattr(editor, 'group_materials', lambda settings, materials: [[raw_id]])
    monkeypatch.setattr(editor, 'research_for_topic', lambda settings, store, title, lead, known, article_id=None: [{'source_id': 'research', 'url': 'https://official.example/doc', 'title': 'Research: x', 'content': 'official detail ' * 60, 'research': True, 'research_engines': ['Firecrawl']}], raising=False)
    def fake_edit(settings, materials, sources):
        captured['materials'] = materials
        return {'title_pl': 'T', 'lead_pl': 'L', 'body_pl': 'Prawdziwa tresc artykulu o faktach i liczbach.', 'category': 'modele', 'sources': sources}
    monkeypatch.setattr(editor, 'edit_article', fake_edit)
    monkeypatch.setattr(editor, 'classify_candidate', lambda *a: {'verdict': 'new', 'article_id': None, 'embedding': [1.0]})
    editor.run(Settings(research_enabled=True), store)
    assert any(m.get('research') for m in captured['materials'])
    assert list(store.research_engines().values()) == [['Firecrawl']]


def test_rich_source_skips_research(tmp_path, monkeypatch):
    import aifeed.editor as editor
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    raw_id = store.upsert_raw('s', 'https://example.org/1', 'Rich news', 1000.0, 'pelny tekst ' * 200, None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    captured = {}
    monkeypatch.setattr(editor, 'group_materials', lambda settings, materials: [[raw_id]])
    monkeypatch.setattr(editor, 'research_for_topic', lambda *a: (_ for _ in ()).throw(AssertionError('research for rich source')), raising=False)
    def fake_edit(settings, materials, sources):
        captured['materials'] = materials
        return {'title_pl': 'T', 'lead_pl': 'L', 'body_pl': 'Prawdziwa tresc artykulu o faktach i liczbach.', 'category': 'modele'}
    monkeypatch.setattr(editor, 'edit_article', fake_edit)
    monkeypatch.setattr(editor, 'classify_candidate', lambda *a: {'verdict': 'new', 'article_id': None, 'embedding': [1.0]})
    editor.run(Settings(research_enabled=True), store)
    assert not any(m.get('research') for m in captured['materials'])


def test_edit_article_records_all_models_used(monkeypatch):
    import aifeed.editor as editor
    import aifeed.llm as llm
    stub = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nKrotkie streszczenie zamiast tlumaczenia.'
    full = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + "Pelne wierne tlumaczenie z faktami. " * 40
    def fake_chat(settings, model, system, user, **kw):
        llm._state.model = model
        return stub if model == 'redaktor' else full
    monkeypatch.setattr(editor, 'chat', fake_chat)
    s = Settings(editor_model='redaktor', editor_fallback_model='drugi-redaktor')
    article = editor.edit_article(s, [{'title': 'T', 'content': 'fakt ' * 400}], [{'name': 'S', 'url': 'https://example.org'}])
    assert article['models'] == ['redaktor', 'drugi-redaktor']


def test_edit_article_dedupes_repeated_model(monkeypatch):
    import aifeed.editor as editor
    import aifeed.llm as llm
    full = '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\n' + "Pelne wierne tlumaczenie z faktami. " * 40
    def fake_chat(settings, model, system, user, **kw):
        llm._state.model = model
        return full
    monkeypatch.setattr(editor, 'chat', fake_chat)
    article = editor.edit_article(Settings(editor_model='redaktor'), [{'title': 'T', 'content': 'fakt ' * 400}], [{'name': 'S', 'url': 'https://example.org'}])
    assert article['models'] == ['redaktor']


def test_editor_prompt_forbids_url_paths_in_prose(monkeypatch):
    import aifeed.editor as editor
    captured = {}
    def fake_chat(settings, model, system, user, **kw):
        captured['system'] = system
        return '{"title_pl":"T","lead_pl":"L","category":"modele","bench":null,"promo":false}\n\n---BODY---\n\nPrawdziwa tresc artykulu o faktach.'
    monkeypatch.setattr(editor, 'chat', fake_chat)
    editor.edit_article(Settings(), [{'title': 'T'}], [{'name': 'S', 'url': 'https://example.org'}])
    assert 'adresów URL' in captured['system']



def test_run_renders_incrementally_after_each_new_article(tmp_path, monkeypatch):
    import aifeed.editor as editor
    from aifeed.store import Store
    store = Store(str(tmp_path / 'db'))
    ids = [store.upsert_raw('s', f'https://x/{i}', f'T{i}', 1000.0+i, 'pelny tekst ' * 100, None) for i in range(2)]
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    monkeypatch.setattr(editor, 'group_materials', lambda settings, materials: [[i] for i in ids])
    out = {'title_pl': 'T', 'lead_pl': 'L', 'body_pl': 'Prawdziwa tresc artykulu o faktach i liczbach.', 'category': 'modele'}
    monkeypatch.setattr(editor, 'edit_article', lambda settings, materials, sources: {**out, 'sources': sources})
    monkeypatch.setattr(editor, 'classify_candidate', lambda *a: {'verdict': 'new', 'article_id': None, 'embedding': [1.0]})
    renders = []
    monkeypatch.setattr(editor, 'write_site', lambda store, out_dir, img_dir=None: renders.append(1), raising=False)
    s = Settings(db_path=str(tmp_path / 'db'), out_dir=str(tmp_path / 'site'), img_dir=str(tmp_path / 'img'))
    assert editor.run(s, store) == 2
    assert len(renders) == 2  # render po każdej nowej grupie, nie tylko na końcu
