import pytest

from aifeed.editor import validate_article, edit_article, group_materials, save_new_article
from aifeed.store import Store


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


def test_group_materials_rejects_missing_or_foreign_ids(monkeypatch):
    import aifeed.editor as editor
    materials = [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(editor, "chat", lambda *args: [[1]])
    with pytest.raises(ValueError):
        group_materials(Settings(), materials)
    monkeypatch.setattr(editor, "chat", lambda *args: [[1, 99]])
    with pytest.raises(ValueError):
        group_materials(Settings(), materials)


def test_group_materials_keeps_complete_partition(monkeypatch):
    import aifeed.editor as editor
    monkeypatch.setattr(editor, "chat", lambda *args: [[1, 2]])
    assert group_materials(Settings(), [{"id": 1}, {"id": 2}]) == [[1, 2]]


def test_edit_article_keeps_all_input_sources(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "A", "url": "https://a.example/1"}, {"name": "B", "url": "https://b.example/2"}]
    output = {"title_pl": "T", "lead_pl": "L", "body_pl": "B", "category": "modele", "sources": sources[:1]}
    monkeypatch.setattr(editor, "chat", lambda *args: output)
    result = edit_article(Settings(), [{"title": "News", "content": "Facts", "url": sources[1]["url"], "source_id": "B"}], sources)
    assert result["sources"] == sources
    assert "—" not in result["title_pl"]


def test_overlong_article_is_shortened_by_editor(monkeypatch):
    import aifeed.editor as editor
    sources = [{"name": "S", "url": "https://example.org"}]
    long = {"title_pl": "T", "lead_pl": "L", "body_pl": " ".join(["słowo"] * 451), "category": "modele"}
    short = {**long, "body_pl": "Krótka treść."}
    responses = iter([long, short])
    calls = []
    def fake_chat(settings, model, system, user):
        calls.append(user)
        return next(responses)
    monkeypatch.setattr(editor, "chat", fake_chat)
    result = editor.edit_article(Settings(), [{"title": "T"}], sources)
    assert result["body_pl"] == "Krótka treść."
    assert len(calls) == 2
    assert "450" in calls[1]


def test_validate_article_rejects_overlong_body():
    article = {"title_pl": "T", "lead_pl": "L", "body_pl": " ".join(["słowo"] * 451),
               "category": "modele", "sources": [{"name": "S", "url": "https://example.org"}]}
    with pytest.raises(ValueError):
        validate_article(article)


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
