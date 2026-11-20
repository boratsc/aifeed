# Editor parallel + checkpoint + meta-guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Przerwany bieg nie powtarza zrobionych grup; wywołania redaktora idą 4 równolegle z własnym backoffem 429; artykuły bez wycieku instrukcji (meta-komentarza).

**Architecture:** Nowa tabela `editor_job` (stan per-grupa, klucz sha256 z raw_id) + ThreadPoolExecutor(4) w `editor.run` z osobnym Store per wątek (WAL). Backoff 429/503 w `llm.chat` (ten sam model, bez gateway-fallbacku). Prompt redakcyjny zakazuje narracji zgodności; `validate_article` odrzuca wąskie wzorce meta.

**Tech Stack:** Python 3.12, stdlib `concurrent.futures`/`hashlib`/`time`/`random`, sqlite3 WAL, httpx, pytest. Zero nowych zależności.

**Spec:** `docs/superpowers/specs/2026-11-20-editor-parallel-checkpoint-design.md`

## Global Constraints

- Zero znaku em dash (—) w polskich tekstach i promptach LLM (zasada odgórna).
- Limit 450 słów body_pl i cała istniejąca walidacja artykułu zostają.
- Model redaktora (mistral przez alias `redaktor`) bez zmian; cron 07:30/17:30 bez zmian.
- `EDITOR_CONCURRENCY=4`, `EDITOR_429_RETRIES=4`, `EDITOR_STALE_SECONDS=1800` (defaults w Settings).
- Przy 429/503 ponawiamy TEN SAM model po backoffie; nigdy nie polegamy na gateway-fallbacku.
- SQLite Connection nie jest thread-safe: każdy worker tworzy własny `Store(db_path)`.
- Testy: `.venv/bin/python -m pytest` (venv w repo).

## Review Focus

- Dwa nakładające się biegi (cron + ręczny): świeży `running` (< stale) musi być pomijany, żeby ta sama grupa nie była redagowana dwa razy równolegle.
- Crash między `mark_done` a `mark_processed`: ścieżka pominięcia `done` musi też oznaczyć materiały jako processed (inaczej wiszą wiecznie w kandydatach).
- Backoff dotyczy wszystkich wywołań `chat` (także triage/dedup): nie może zepsuć retry JSON ani nie-429 błędów (400 ma przepaść od razu).
- Wyścigi zapisów SQLite z 4 wątków: WAL + krótkie commity; test z wieloma grupami na realnej bazie tmp.
- Meta-guard false positive: fakt „Google nie podało ceny" MUSI przejść walidację (meta mówi o materiale/artykule, fakt o produkcie).

---

### Task 1: Store — tabela editor_job + config

**Files:**
- Modify: `aifeed/store.py` (SCHEMA + metody + `self.db_path`)
- Modify: `aifeed/config.py` (3 pola + from_env)
- Test: `tests/test_store.py`, `tests/test_config.py` (istnieje? jeśli nie: dopisz do test_store.py)

**Interfaces:**
- Produces: `Store.db_path: str`; `Store.job(group_key) -> sqlite3.Row | None` (kolumny `state`, `started_at`, `article_id`); `Store.mark_running(group_key)`; `Store.mark_done(group_key, article_id)`; `Store.mark_failed(group_key)`; `Settings.editor_concurrency=4`, `Settings.editor_429_retries=4`, `Settings.editor_stale_seconds=1800` (env: `EDITOR_CONCURRENCY`, `EDITOR_429_RETRIES`, `EDITOR_STALE_SECONDS`, int).

- [ ] **Step 1: failing test** (dopisz do `tests/test_store.py`):

```python
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


def test_settings_editor_defaults(monkeypatch):
    from aifeed.config import Settings
    for var in ("EDITOR_CONCURRENCY", "EDITOR_429_RETRIES", "EDITOR_STALE_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert (s.editor_concurrency, s.editor_429_retries, s.editor_stale_seconds) == (4, 4, 1800)
    monkeypatch.setenv("EDITOR_CONCURRENCY", "2")
    assert Settings.from_env().editor_concurrency == 2
```

- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/test_store.py -q` — Expected: FAIL (brak `db_path`/`job`).
- [ ] **Step 3:** Implementacja: do SCHEMA dopisz tabelę:

```sql
CREATE TABLE IF NOT EXISTS editor_job (
    group_key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    started_at REAL,
    updated_at REAL,
    article_id INTEGER
);
```

W `__init__`: `self.db_path = db_path`. Metody (upsert przez `ON CONFLICT(group_key) DO UPDATE`, `time.time()`, commit po każdym; `mark_running` ustawia `article_id=NULL`). Config: 3 pola z defaults + odczyt int z env w `from_env`.

- [ ] **Step 4:** Run suite `.venv/bin/python -m pytest -q` — Expected: PASS (wszystkie).
- [ ] **Step 5:** Commit `feat: editor_job checkpoint table + editor settings`.

### Task 2: llm — backoff 429/503

**Files:**
- Modify: `aifeed/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `Settings.editor_429_retries` (Task 1).
- Produces: `chat(...)` bez zmiany sygnatury; wewnętrzny retry HTTP: statusy 429 i 503 → `time.sleep(2 ** (n + 1) + random.uniform(0, 1))` dla n=0..retries-1, potem re-raise `httpx.HTTPStatusError`. Inne statusy przebijają natychmiast. Retry JSON bez zmian. `import time, random` na górze modułu (testy patchują `aifeed.llm.time.sleep` i `aifeed.llm.random.uniform`).

- [ ] **Step 1: failing tests** (dopisz do `tests/test_llm.py`):

```python
def test_chat_backs_off_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429)
        return httpx.Response(200, content=_sse('{"ok": true}').encode(),
                              headers={"content-type": "text/event-stream"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    slept = []
    import aifeed.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(llm.random, "uniform", lambda a, b: 0.0)
    assert chat(make_settings(), "m", "sys", "usr") == {"ok": True}
    assert calls["n"] == 3
    assert slept == [2.0, 4.0]


def test_chat_gives_up_after_429_retries(monkeypatch):
    def handler(request):
        return httpx.Response(429)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    import aifeed.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        chat(make_settings(), "m", "sys", "usr")


def test_chat_does_not_retry_400(monkeypatch):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(400)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPStatusError):
        chat(make_settings(), "m", "sys", "usr")
    assert calls["n"] == 1
```

- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/test_llm.py -q` — Expected: nowe FAIL (429 nieponawiane; `llm.time` nie istnieje).
- [ ] **Step 3:** W `chat` owiń blok `with client.stream(...)` wewnętrzną pętlą `for http_attempt in range(settings.editor_429_retries + 1):` z `try/except httpx.HTTPStatusError`: jeśli status nie w {429, 503} lub ostatnia próba → `raise`; inaczej sleep(backoff) i continue. Zbieranie SSE bez zmian; po udanym streamie `break`.
- [ ] **Step 4:** Run `.venv/bin/python -m pytest -q` — Expected: PASS wszystkie (stare testy llm też).
- [ ] **Step 5:** Commit `feat: client-side 429/503 backoff in chat, same model on retry`.

### Task 3: meta-guard — prompt + walidacja

**Files:**
- Modify: `aifeed/editor.py` (`edit_article` system, `validate_article`)
- Test: `tests/test_editor.py`

**Interfaces:**
- Produces: `editor.META_PATTERNS: tuple[str, ...]` = `("temat został zakwalifikowany", "artykuł opiera się wyłącznie", "w materiale nie zamieszczono", "materiał nie zawiera danych", "opublikowany opis koncentruje się")`. `validate_article` sprawdza `body_pl.lower()` pod kątem zawiera-którykolwiek → `ValueError("editorial meta-commentary in body")`. Bez wycinania akapitów.

- [ ] **Step 1: failing tests**:

```python
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
    def fake_chat(settings, model, system, user):
        captured["system"] = system
        return {"title_pl": "T", "lead_pl": "L", "body_pl": "B", "category": "modele"}
    monkeypatch.setattr(editor, "chat", fake_chat)
    editor.edit_article(Settings(), [{"title": "N"}], [{"name": "S", "url": "https://example.org"}])
    assert "Nie wspominaj o ich braku" in captured["system"]
    assert "Nie opisuj procesu redakcji" in captured["system"]
```

- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/test_editor.py -q` — Expected: 3 nowe FAIL.
- [ ] **Step 3:** W system `edit_article` zamień linię `Nie dodawaj wyników benchmarków ani cen, których nie ma w materiałach. ` na:
`Nie dodawaj wyników benchmarków ani cen, których nie ma w materiałach. Nie wspominaj o ich braku, po prostu ich nie wymieniaj. Nie opisuj procesu redakcji, klasyfikacji ani metodologii. Nie pisz zdań typu 'temat został zakwalifikowany', 'artykuł opiera się wyłącznie', 'w materiale nie zamieszczono'. Pisz wyłącznie fakty o produkcie lub narzędziu. `
Dodaj `META_PATTERNS` i sprawdzenie w `validate_article` po walidacji pól, przed limitem słów.
- [ ] **Step 4:** Run `.venv/bin/python -m pytest -q` — Expected: PASS wszystkie.
- [ ] **Step 5:** Commit `feat: forbid and reject editorial meta-commentary in article body`.

### Task 4: editor.run — checkpoint + ThreadPool(4)

**Files:**
- Modify: `aifeed/editor.py` (`run`, nowy `group_key`, nowy `_process_group`)
- Test: `tests/test_editor.py`

**Interfaces:**
- Consumes: Task 1 (`Store.db_path`, `job`, `mark_running/done/failed`, `Settings.editor_*`), Task 3 (meta ValueError łapany jak 450).
- Produces: `editor.group_key(ids: list[int]) -> str` = `hashlib.sha256(",".join(str(i) for i in sorted(ids)).encode()).hexdigest()`. `run(settings, store)` — sygnatura bez zmian, zwraca licznik nowych artykułów. `_process_group(settings, db_path, key, group, sources, image) -> int` tworzy własny `Store(db_path)`.

Algorytm `run`:
1. `materials = store.relevant_candidates()`; pusto → 0.
2. `groups = group_materials(settings, materials)` (bez zmian, 1 wywołanie).
3. Dla każdej grupy: `key = group_key(ids)`; `job = store.job(key)`:
   - `done` → `mark_processed` dla każdego materiału grupy (recover po crashu między done a processed), pomiń;
   - `running` i `time.time() - started_at < settings.editor_stale_seconds` → pomiń (świeży claim);
   - inaczej (brak / `failed` / stale `running`) → `store.mark_running(key)` (claim w głównym wątku) i do kolejki zadań.
4. `ThreadPoolExecutor(max_workers=settings.editor_concurrency)`; submit `_process_group` per grupa; suma `future.result()`.

`_process_group` = obecne ciało pętli (edit_article → image → classify_candidate → new/extension/merge/same → mark_processed) na własnym Store, plus:
- sukces (new/extension/merge/same) → `mark_done(key, article_id)` PO `mark_processed` materiałów;
- **same:** dotychczasowy `continue` pomijał mark_processed (bug: materiał wracał co bieg). Teraz: mark_processed + mark_done(key, decision["article_id"]), return 0;
- `ValueError` z `edit_article` (walidacja/meta/450) → `store.mark_failed(key)`, `logging.warning`, return 0; materiał zostaje w kandydatach.

- [ ] **Step 1: failing tests** (wspólny helper setup jak w istniejących testach: raw triaged/relevant; monkeypatch `editor.group_materials`, `editor.edit_article`, `editor.classify_candidate`):

```python
def test_done_group_is_skipped_and_recovered(tmp_path, monkeypatch):
    import aifeed.editor as editor
    store = Store(str(tmp_path / "db"))
    raw_id = store.upsert_raw("s", "https://example.org/1", "T", 1000.0, "F", None)
    store.db.execute("UPDATE raw SET triaged=1,relevant=1,category='modele',summary_pl='F'")
    store.db.commit()
    key = editor.group_key([raw_id])
    store.mark_done(key, 1)
    def boom(*a):
        raise AssertionError("edit_article called for done group")
    monkeypatch.setattr(editor, "edit_article", boom)
    assert editor.run(Settings(), store) == 0
    assert store.relevant_candidates() == []  # recovery: processed oznaczone


def test_fresh_running_group_is_skipped(tmp_path, monkeypatch):
    # jak wyżej, ale mark_running(key) zamiast mark_done; edit_article = boom
    # assert run == 0 i kandydat NADAL w kolejce (nie processed)


def test_stale_running_group_is_retried(tmp_path, monkeypatch):
    # mark_running(key); UPDATE editor_job SET started_at = started_at - 99999
    # edit_article zwraca poprawny artykuł, classify_candidate verdict new
    # assert run == 1, job state done


def test_failed_group_is_retried_next_run(tmp_path, monkeypatch):
    # pierwszy run: edit_article raise ValueError("article exceeds 450 words")
    # assert job state failed, kandydat zostaje
    # drugi run: edit_article OK → run == 1, job done


def test_same_verdict_marks_processed(tmp_path, monkeypatch):
    # istniejący artykuł + classify_candidate verdict same
    # assert run == 0 i relevant_candidates() == [] (regresja: stary continue gubił processed)


def test_run_uses_configured_concurrency(tmp_path, monkeypatch):
    # monkeypatch editor.ThreadPoolExecutor klasą przechwytującą max_workers
    # 2 grupy, reszta jak istniejące testy; assert captured max_workers == Settings().editor_concurrency
```

(Uwaga: testy 2–6 rozpisz pełnym kodem wzorem pierwszego i istniejących testów w pliku; każdy ma własny tmp_path Store i monkeypatche. `group_key` test: `test_group_key_is_order_insensitive`: `group_key([2,1]) == group_key([1,2]) != group_key([1])`.)

- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/test_editor.py -q` — Expected: nowe FAIL (`group_key` nie istnieje).
- [ ] **Step 3:** Implementacja wg algorytmu wyżej. `from concurrent.futures import ThreadPoolExecutor`, `import hashlib, time`. Istniejące testy editor (merge/extension/invalid/450/group) MUSZĄ przejść bez zmian ich asercji.
- [ ] **Step 4:** Run `.venv/bin/python -m pytest -q` — Expected: PASS wszystkie.
- [ ] **Step 5:** Commit `feat: per-group checkpoint + parallel editor with stale-claim recovery`.

### Task 5: deploy CT110 + weryfikacja na żywym backlogu

**Files:**
- Modify (zdalnie): `/srv/aifeed/app/aifeed/{store,config,llm,editor}.py` na CT110
- Backup przed podmianą: `cp file file.bak-YYYYMMDD-HHMMSS` (wzór istniejących .bak).

- [ ] **Step 1:** `scp aifeed/{store,config,llm,editor}.py bartek@192.168.1.100:/tmp/` → `ssh bartek@192.168.1.100 "sudo pct exec 110 -- sh -c 'cp ... .bak-...'"` → `sudo pct push 110 /tmp/<f> /srv/aifeed/app/aifeed/<f>` ×4.
- [ ] **Step 2:** Sanity na CT110: `cd /srv/aifeed/app && .venv/bin/python -c "from aifeed import editor, llm, store; print('imports ok')"`.
- [ ] **Step 3:** Uruchom pipeline raz (backlog: 2 unprocessed relevant): `AIFEED_ENV=/srv/aifeed/.env .venv/bin/python -m aifeed` (foreground, timeout do 600000 ms; jeśli dłużej — run_in_background i czekaj na notyfikację).
- [ ] **Step 4:** Weryfikacja: `SELECT group_key,state,article_id FROM editor_job` — wszystkie `done`/`failed`, żadne wieczne `running`; `SELECT count(*) FROM raw WHERE triaged=1 AND relevant=1 AND id NOT IN (SELECT raw_id FROM processed_raw)` = liczba materiałów failed grup (0 jeśli wszystko OK); nowe artykuły bez meta-wzorców (`grep -i "zakwalifikowany\|opiera się wyłącznie\|nie zamieszczono" out/a/*.html` — brak).
- [ ] **Step 5:** Commit (jeśli były poprawki po deployu) + raport: czasy biegu z run_log vs poprzednie 599 s.