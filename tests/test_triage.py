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
    # nieznana kategoria nie zabija batcha; item jest potraktowany jako nieistotny
    out = validate_triage([{"raw_id": 1, "relevant": True, "category": "other", "summary_pl": "X"}], {1})
    assert out[0]["relevant"] is False
    assert out[0]["category"] is None


def test_validate_triage_accepts_irrelevant_without_category():
    result = [{"raw_id": 1, "relevant": False, "category": None, "summary_pl": ""}]
    assert validate_triage(result, {1}) == result


def test_categories_includes_promocje():
    from aifeed.triage import CATEGORIES
    assert "promocje" in CATEGORIES


def test_classify_batch_prompt_mentions_promocje(monkeypatch):
    import aifeed.triage as triage
    captured = {}
    def fake_chat(settings, model, system, user):
        captured["system"] = system
        return [{"raw_id": 1, "relevant": False, "category": None, "summary_pl": ""}]
    monkeypatch.setattr(triage, "chat", fake_chat)
    triage.classify_batch(Settings(), [RawItem(1, "s", "https://x", "T", 1.0, "C", None)])
    # niepusty batch wysyła prompt z regułą promocji
    assert "promocje" in captured["system"]


def test_validate_triage_normalizes_modeles_to_modele():
    from aifeed.triage import validate_triage
    out = validate_triage([{"raw_id": 1, "relevant": True, "category": "modeles", "summary_pl": "x"}], {1})
    assert out[0]["category"] == "modele"


def test_promo_sources_forced_to_promocje():
    from aifeed.sources import PROMO_SOURCES
    import aifeed.triage as triage
    # imituj item z promocyjnego źródła
    from aifeed.store import RawItem
    r = RawItem(1, "aitkradar_deals", "https://x", "Promo", 1.0, "c", None)
    cat = triage.resolve_category("cenniki", r.source_id)
    assert cat == "promocje"


def test_news_sources_keep_model_category():
    import aifeed.triage as triage
    from aifeed.store import RawItem
    r = RawItem(1, "openai", "https://x", "News", 1.0, "c", None)
    assert triage.resolve_category("modele", r.source_id) == "modele"


def test_resolve_category_maps_agenci_to_narzedzia():
    import aifeed.triage as triage
    assert triage.resolve_category("agenci", "openai") == "narzedzia"


def test_resolve_category_maps_agent_synonyms():
    import aifeed.triage as triage
    for syn in ("agenci", "agenty", "agent", "narzedzie"):
        assert triage.resolve_category(syn, "openai") == "narzedzia", syn


def test_validate_triage_drops_unknown_category_instead_of_crashing():
    from aifeed.triage import validate_triage
    out = validate_triage(
        [{"raw_id": 1, "relevant": True, "category": "totally_unknown_xyz", "summary_pl": "x"}],
        {1}, {1: "openai"})
    # nie crashuje; nieznana kategoria -> relevant False
    assert out[0]["relevant"] is False
    assert out[0]["category"] is None


def test_map_category_via_llm_remaps_unknown(monkeypatch):
    import aifeed.triage as triage
    from aifeed.config import Settings
    monkeypatch.setattr(triage, "chat", lambda settings, model, system, user, **kw: {"category": "narzedzia"})
    assert triage.map_category_via_llm(Settings(), "agenci") == "narzedzia"


def test_map_category_via_llm_returns_none_on_garbage(monkeypatch):
    import aifeed.triage as triage
    from aifeed.config import Settings
    monkeypatch.setattr(triage, "chat", lambda settings, model, system, user, **kw: {"category": "bad"})
    assert triage.map_category_via_llm(Settings(), "agenci") is None


def test_validate_triage_remaps_unknown_via_llm_when_settings_present(monkeypatch):
    import aifeed.triage as triage
    from aifeed.config import Settings
    monkeypatch.setattr(triage, "map_category_via_llm", lambda s, c: "narzedzia")
    out = triage.validate_triage(
        [{"raw_id": 1, "relevant": True, "category": "agenci", "summary_pl": "x"}],
        {1}, {1: "openai"}, Settings())
    assert out[0]["relevant"] is True
    assert out[0]["category"] == "narzedzia"
