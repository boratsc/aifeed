# Editor: równoległość + checkpointy (idempotencja) + strażnik meta-komentarza — design

**Data:** 2026-11-20
**Status:** do zatwierdzenia
**Rozmiar:** mała zmiana (modyfikacja `editor.py`, `llm.py`, `store.py`; bez nowej warstwy kolejki)

## Intencja i dlaczego

Bartek zgłosił: model redaktor (mistral reasoning przez gateway alias `redaktor`)
przerzuca feedy ~10 min każdy, więc cron „ciągle przerzuwa te same feedy". Cel
wybrany przez Bartka: **nie przerabiać już raz zrobionych** (idempotencja +
checkpointy), a **nie** zamieniać cron na kolejkę feedów ani na ciągły strumień.

Zachować mistrala na redaktorze (jakość bez zmian). Poprawić tylko:
1. sekwencyjność wywołań edytora (grupy newsów idą jedna po drugiej),
2. brak checkpointu w trakcie grupy (przerwany bieg traci pracę i powtarza ją).

## Fakty (zweryfikowane na CT110)

- `pipeline.run` → fetch/triage (już idempotentne per-batch) → `editor.run` → render.
- `editor.run`: dla **każdej grupy** newsów sekwencyjnie:
  `edit_article` (redaktor, mistral, ~10 min) → `classify_candidate`
  (embedding ollama + ewentualnie `decide` chat).
- `mark_processed` wołane **dopiero po** zapisaniu artykułu (koniec pętli per-grupa).
  Przerwanie w środku `edit_article` → grupa nie trafia do `processed_raw` →
  przy następnym biegu przerabiana od nowa. To jest „ciągłe przerzuwanie".
- `run_log`: jeden bieg trwał 599 s, dał 8 artykułów.
- Baza: 96 raw / 96 triaged / 40 relevant / 38 processed / 38 articles /
  **2 unprocessed relevant** (pozostałość po przerwaniu).
- Gateway `redaktor`: piramida fill-first (qwen → deepseek → cf-mistral → NIM
  mistral-large-4-1t-a52b → free). Znana pułapka (diagnoza Bartka 2026-11-20):
  fallback z Qwen reasoning na DeepSeek bez thinking daje 400 „must be passed back".
- `llm.chat`: retry tylko błędów JSON; `raise_for_status()` przy 429 rzuca
  `HTTPStatusError` bez backoff.

## Decyzje Bartka (wiązanie)

- **Zostaw mistral** na redaktorze — nie tykaj jakości modelu.
- **Limit 4 równolegle max** (NIM ma limit concurrent, nie tylko req/s).
- **Własny backoff na 429** zamiast polegać na gateway-fallbacku
  (żeby nie mieszać modeli mid-stream i nie ryzykować 400).

## Podejścia rozważone

- **A. Zrównoleglenie wywołań + checkpoint (wybrane).** ThreadPoolExecutor
  `max_workers=4` na pętli per-grupa w `editor.run`. Grupy są niezależne.
  Checkpoint `in_progress`/`done` przed/po `edit_article`. Najkrótszy kod,
  rozwiązuje oba bóle, bez nowej warstwy kolejki.
- **B. Trwała kolejka per-grupa + worker.** Nowa tabela `editor_jobs`, retry,
  widok postępu. Więcej kodu i powierzchni błędów; dla ~10 grup/dzień przerost.
  Bartek wykluczył ciągły strumień, więc B nie jest potrzebne.

Odrzucone: kolejka feedów / ciągły strumień (Bartek wybrał idempotencję, nie stream).

## Projekt (jak zbudowane)

### 1. Checkpoint per-grupa (`store.py`)
Nowa tabela `editor_job`:
- `group_key TEXT PRIMARY KEY` — `sha256(",".join(sorted(str(id) for id in ids)))`,
  deterministyczny dla tego samego zestawu raw_id. Wybór Bartka: hash raw_id
  (proste; merge/extension z nowym newsem da nowy klucz i przerobi grupę, ale
  to rzadkie i akceptowalne).
- `state TEXT` — `pending` / `running` / `done` / `failed`.
- `started_at REAL`, `updated_at REAL`.
- `article_id INTEGER` (nullable) — po sukcesie.

Logika w `editor.run`:
- Przed `edit_article` dla grupy: `mark_running(group_key)` (INSERT ON CONFLICT
  UPDATE state='running', started_at=now). Jeśli grupa już `done` → pomiń.
- Po sukcesie (zapis artykułu + dedup): `mark_done(group_key, article_id)`.
- `relevant_candidates()` rozszerzone: pomija grupy, których klucz jest już
  `done`. Stale `running` (started_at starsze niż próg, np. 2 × max wywołanie)
  jest ponownie brane pod uwagę — to obsługuje restart w trakcie.
- `mark_processed` (stary mechanizm `processed_raw`) zostaje jako drugorzędny
  filtr bezpieczeństwa; `editor_job` jest źródłem prawdy dla idempotencji.

### 2. Równoległość (`editor.py`)
- `group_materials` (1 wywołanie) bez zmian, na początku.
- Pętla `for ids in ...` → `ThreadPoolExecutor(max_workers=4)`; każda grupa to
  osobne zadanie `process_group(...)`, które robi: `edit_article` →
  `classify_candidate` → zapis → `mark_done`/`mark_processed`.
- Zapisy do SQLite: każdy worker używa **własnego połączenia** (Store per
  wątek), bo `sqlite3.Connection` nie jest thread-safe. Albo wspólna kolejka
  zapisów z lockiem — wybór: **Store per wątek** (prostsze, WAL znosi równoległe
  zapisy).
- Wyniki zbierane po `as_completed`, licznik `count` na końcu.

### 3. Backoff na 429 (`llm.py`)
- W `chat`, wokół `client.stream(...)`: łapać `httpx.HTTPStatusError` z
  `status_code == 429` (i 503 jako „try later") → `time.sleep(backoff)` z
  wykładniczym backoffem (np. 2, 4, 8 s) + jitter, max N prób (np. 4).
- Nie polegać na gateway-fallback: przy 429 **ten sam model** ponawiany po
  backoff, nie zepchnięty na inny provider mid-stream.
- Backoff nie blokuje innych workerów (każdy wątek śpi niezależnie).
- Zachować istniejący retry błędów JSON.

### 4. Konfiguracja
- `Settings.editor_concurrency: int = 4` (env `EDITOR_CONCURRENCY`).
- `Settings.editor_429_retries: int = 4` (env `EDITOR_429_RETRIES`).
- `Settings.editor_stale_seconds: int = 1800` (próg „stale running"; 2 × ~10 min).

## Cele / nie-cele

**Cele:** idempotencja (przerwany bieg nie powtarza done grup), równoległość
4 wątków, własny backoff 429, bez zmiany jakości modelu, bez nowej kolejki.

**Nie-cele:** ciągły strumień, per-feed procesor, zmiana modelu redaktora,
kolejka zadań z osobnym workerem (Wariant B).

**Anty-cele:** cicha utrata newsów (grupa `failed` musi zostać w stanie, żeby
było ją widać w logu), race na SQLite (Connection per wątek), mieszanie modeli
przy 429 (własny backoff, nie gateway-fallback).

## Ograniczenia

- Gateway NIM: limit concurrent (przyjąć 4 jako bezpieczny sufit).
- Znany 400 przy fallback reasoning→non-reasoning (nie prowokować go).
- SQLite: Connection nie thread-safe → Store per wątek (WAL).
- Cron 07:30 / 17:30 zostaje bez zmian; to nie jest zmiana harmonogramu.

## Co zostawiam builderowi

- Wartości backoffu (sugerowane 2/4/8 s + jitter) — builder dobiera.
- Czy `process_group` zwraca wynik czy zapisuje bezpośrednio (wybór: zapis
  bezpośrednio + zwraca bool/count dla licznika).

---

## Część 2: strażnik meta-komentarza (instruction-echo)

### Problem (zweryfikowany na żywo)

Słabe modele (flash/mistral) zamiast stosować zakaz „nie dodawaj benchmarków/cen,
których nie ma", **narrują swoją zgodność** — wyciekają instrukcje do treści:

- Gemini: „W materiale nie zamieszczono danych dotyczących wydajności, porównań
  z innymi metodami wyboru narzędzi ani cen za korzystanie z nowej funkcji…"
- HF: „Temat został zakwalifikowany jako narzędzie. Artykuł opiera się wyłącznie
  na opisie zamieszczonym przez Hugging Face."

To meta-komentarz o procesie redakcyjnym/klasyfikacji, nie treść artykułu.

### Decyzja Bartka

**Prompt + wąski odrzut oczywistych meta.** Nie wycinamy agresywnie (żeby nie
stracić faktów typu „Google nie podało ceny" — to fakt o produkcie, nie meta).

### Granica meta vs fakt (wiązanie)

- **META (odrzuć):** narracja o procesie redakcji/klasyfikacji/metodologii:
  „temat został zakwalifikowany", „artykuł opiera się wyłącznie", „w materiale
  nie zamieszczono danych", „materiał nie zawiera danych o…", „opublikowany opis
  koncentruje się na".
- **FAKT (zachowaj):** informacja o produkcie/narzędziu, w tym o braku danych
  podanych przez producenta: „Google nie podało ceny", „brak danych o limitach",
  „funkcja dostępna w preview". Różnica: meta mówi o *materiale/artykule/procesie*,
  fakt mówi o *produkcie/firmie*.

### Warstwa 1 — prompt (`editor.py`, `edit_article` system)

Doprecyzować zakazy, żeby model nie narruł zgodności:
- „Nie dodawaj wyników benchmarków ani cen, których nie ma w materiałach.
  **Nie wspominaj o ich braku — po prostu ich nie wymieniaj.**"
- „Nie opisuj procesu redakcji, klasyfikacji ani metodologii. Nie pisz zdań
  typu 'temat został zakwalifikowany', 'artykuł opiera się wyłącznie',
  'w materiale nie zamieszczono'. Pisz wyłącznie fakty o produkcie/narzędziu."

### Warstwa 2 — walidacja (`editor.py`, `validate_article`)

Wąski, deterministyczny odrzut oczywistych meta-wzorców (case-insensitive,
na poziomie całego `body_pl`):
- `temat został zakwalifikowany`
- `artykuł opiera się wyłącznie`
- `w materiale nie zamieszczono`
- `materiał nie zawiera danych`
- `opublikowany opis koncentruje się`

Jeśli `body_pl` zawiera którykolwiek → `raise ValueError("editorial meta-commentary
in body")`. `editor.run` łapie to jak „450 words" (pomija grupę, materiał zostaje
w kolejce). Nie wycinamy akapitów — odrzut całości, żeby nie ryzykować utraty
faktów przez regex.

### Cele / anty-cele części 2

**Cel:** artykuły bez meta-komentarza o procesie; fakty o produkcie (w tym
„producent nie podał X") zachowane.

**Anty-cel:** agresywne wycinanie akapitów (ryzyko utraty faktów); odrzut
newsów, które mają tylko fakt „brak danych od producenta" (to nie meta).