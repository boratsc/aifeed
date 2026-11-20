import pytest

from aifeed.triage import classify_batch, validate_triage
from aifeed.config import Settings
from aifeed.store import RawItem


def test_run_persists_validated_summary_and_category(tmp_path, monkeypatch):
    import aifeed.triage as triage
    from aifeed.store import Store
    store = Store(str(tmp_path / "db"))
    raw_id = store.upsert_raw("source", "https://example.org/1", "T", 1.0, "C", None)
    monkeypatch.setattr(triage, "chat", lambda *args: [{"raw_id": raw_id, "relevant": True, "category": "modele", "summary_pl": "Nowy model."}])
    assert triage.run(Settings(), store) == 1
    row = store.db.execute("SELECT triaged, category, summary_pl FROM raw WHERE id = ?", (raw_id,)).fetchone()
    assert tuple(row) == (1, "modele", "Nowy model.")


def test_invalid_triage_does_not_mark_any_items(tmp_path, monkeypatch):
    import aifeed.triage as triage
    from aifeed.store import Store
    store = Store(str(tmp_path / "db"))
    raw_id = store.upsert_raw("source", "https://example.org/1", "T", 1.0, "C", None)
    monkeypatch.setattr(triage, "chat", lambda *args: [{"raw_id": raw_id, "relevant": False, "summary_pl": ""}])
    with pytest.raises(ValueError):
        triage.run(Settings(), store)
    assert len(store.untriaged()) == 1


def test_classify_batch_passes_untrusted_items_and_validates(monkeypatch):
    import aifeed.triage as triage
    calls = []
    def fake_chat(settings, model, system, user):
        calls.append((model, system, user))
        return [{"raw_id": 7, "relevant": True, "category": "modele", "summary_pl": "Nowy model."}]
    monkeypatch.setattr(triage, "chat", fake_chat)
    items = [RawItem(7, "source", "https://example.org/1", "Tytuł", 1.0, "Treść", None)]
    results = classify_batch(Settings(), items)
    assert results[0]["raw_id"] == 7
    assert calls[0][0] == "deepseek-flash-direct"
    assert "Tytuł" in calls[0][2]
    assert "nie" in calls[0][1].lower()
    assert "programowania" in calls[0][1]
    assert "NAND" in calls[0][1]
    assert "sponsorowane" in calls[0][1]


def test_classify_batch_rejects_incomplete_model_response(monkeypatch):
    import aifeed.triage as triage
    monkeypatch.setattr(triage, "chat", lambda *args: [])
    items = [RawItem(7, "source", "https://example.org/1", "T", 1.0, "C", None)]
    with pytest.raises(ValueError):
        classify_batch(Settings(), items)


def test_validate_triage_accepts_only_known_unique_ids():
    results = [
        {"raw_id": 1, "relevant": True, "category": "modele", "summary_pl": "Nowy model."},
        {"raw_id": 1, "relevant": True, "category": "modele", "summary_pl": "Powtórka."},
        {"raw_id": 999, "relevant": True, "category": "modele", "summary_pl": "Obcy."},
    ]
    with pytest.raises(ValueError):
        validate_triage(results, {1, 2})


def test_validate_triage_rejects_unknown_category():
    with pytest.raises(ValueError):
        validate_triage([{"raw_id": 1, "relevant": True, "category": "other", "summary_pl": "X"}], {1})


def test_validate_triage_accepts_irrelevant_without_category():
    result = [{"raw_id": 1, "relevant": False, "category": None, "summary_pl": ""}]
    assert validate_triage(result, {1}) == result
