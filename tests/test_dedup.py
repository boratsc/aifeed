import pytest

from aifeed.dedup import candidate_matches, decide, validate_decision, classify_candidate
from aifeed.store import Store
import time


def test_classify_candidate_finds_related_article(tmp_path, monkeypatch):
    import aifeed.dedup as dedup
    store = Store(str(tmp_path / "db"))
    aid = store.add_article("a", "A", "lead", "body", "modele", [], None, None, [1.0, 0.0], time.time(), None)
    monkeypatch.setattr(dedup, "vector", lambda settings, text: [1.0, 0.0])
    monkeypatch.setattr(dedup, "chat", lambda *args: {"verdict": "same", "article_id": aid})
    decision = classify_candidate(Settings(), store, {"title": "A", "summary_pl": "lead"})
    assert decision["verdict"] == "same"
    assert decision["article_id"] == aid
from aifeed.config import Settings


def test_lower_similarity_does_not_hide_possible_duplicate(tmp_path, monkeypatch):
    import aifeed.dedup as dedup
    store = Store(str(tmp_path / "db"))
    aid = store.add_article("a", "A", "lead", "body", "modele", [], None, None, [1.0, 0.0], time.time(), None)
    monkeypatch.setattr(dedup, "vector", lambda settings, text: [0.8, 0.6])
    monkeypatch.setattr(dedup, "chat", lambda *args: {"verdict": "same", "article_id": aid})
    decision = classify_candidate(Settings(), store, {"title": "A", "summary_pl": "lead"})
    assert decision["verdict"] == "same"


def test_decide_does_not_call_model_without_candidates(monkeypatch):
    import aifeed.dedup as dedup
    monkeypatch.setattr(dedup, "chat", lambda *args: pytest.fail("model called without candidates"))
    assert decide(Settings(), {"title": "T", "summary": "S"}, []) == {"verdict": "new", "article_id": None}


def test_decide_validates_related_article(monkeypatch):
    import aifeed.dedup as dedup
    monkeypatch.setattr(dedup, "chat", lambda *args: {"verdict": "extension", "article_id": 4})
    result = decide(Settings(), {"title": "T", "summary": "S"}, [{"id": 4, "title_pl": "A", "lead_pl": "L"}])
    assert result["verdict"] == "extension"


def test_decision_accepts_existing_related_article():
    assert validate_decision({"verdict": "extension", "article_id": 4}, {4}) == {"verdict": "extension", "article_id": 4}


def test_decision_rejects_unknown_relation_or_article():
    with pytest.raises(ValueError):
        validate_decision({"verdict": "same", "article_id": 9}, {4})
    with pytest.raises(ValueError):
        validate_decision({"verdict": "unknown", "article_id": None}, {4})
    with pytest.raises(ValueError):
        validate_decision({"verdict": "new", "article_id": 4}, {4})


def test_candidate_matches_uses_similarity_and_keeps_best_first():
    matches = candidate_matches([1.0, 0.0], [(1, [1.0, 0.0]), (2, [0.0, 1.0]), (3, [0.9, 0.1])], 0.86)
    assert [item[0] for item in matches] == [1, 3]


def test_candidate_matches_rejects_different_dimensions():
    # nie crashuje; pomija kandydatów z niezgodnym wymiarem (stub [1.0])
    assert candidate_matches([1.0, 0.0], [(1, [1.0])], 0.86) == []


def test_candidate_matches_skips_mismatched_dimensions():
    from aifeed.dedup import candidate_matches
    query = [1.0, 0.0, 0.0]
    embeddings = [(1, [1.0]), (2, [1.0, 0.0, 0.0])]  # id1 ma dim=1 (stub), id2 pasuje
    assert candidate_matches(query, embeddings, 0.9) == [(2, 1.0)]


def test_release_of_same_base_version_merges_without_model(tmp_path, monkeypatch):
    import aifeed.dedup as dedup
    from aifeed.dedup import release_keys
    url = "https://github.com/github/copilot-cli/releases/tag/v1.0.118-%d"
    assert release_keys([url % 0]) == release_keys([url % 2])
    store = Store(str(tmp_path / "db"))
    aid = store.add_article("a", "A", "lead", "body", "modele", [{"url": url % 0}], None, None, [1.0], time.time(), None)
    monkeypatch.setattr(dedup, "vector", lambda settings, text: [0.0, 1.0])
    monkeypatch.setattr(dedup, "chat", lambda *args: pytest.fail("model called"))
    decision = classify_candidate(Settings(), store, {"title": "B", "summary_pl": "x", "urls": [url % 2]})
    assert (decision["verdict"], decision["article_id"]) == ("merge", aid)
