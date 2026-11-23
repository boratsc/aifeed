"""SQLite state: raw items, articles, prices, run log. One file, WAL mode."""
import json
import math
import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    published_at REAL,
    content TEXT,
    og_image TEXT,
    triaged INTEGER DEFAULT 0,
    relevant INTEGER,
    category TEXT
);
CREATE TABLE IF NOT EXISTS article (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title_pl TEXT NOT NULL,
    lead_pl TEXT,
    body_pl TEXT,
    category TEXT,
    sources_json TEXT,
    bench_json TEXT,
    promo TEXT,
    embedding_json TEXT,
    image TEXT,
    published_at REAL,
    delta_of INTEGER REFERENCES article(id),
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS price (
    vendor TEXT NOT NULL,
    model TEXT NOT NULL,
    price_in REAL,
    price_out REAL,
    currency TEXT,
    seen_at REAL,
    PRIMARY KEY (vendor, model)
);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY,
    vendor TEXT,
    model TEXT,
    old_in REAL,
    old_out REAL,
    new_in REAL,
    new_out REAL,
    seen_at REAL
);
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY,
    started_at REAL,
    finished_at REAL,
    new_count INTEGER,
    errors_json TEXT
);
CREATE TABLE IF NOT EXISTS tool_version (
    repo TEXT PRIMARY KEY,
    tag TEXT,
    fetched_at REAL
);
CREATE TABLE IF NOT EXISTS research_engines (
    article_id INTEGER PRIMARY KEY,
    engines TEXT
);
CREATE TABLE IF NOT EXISTS editor_job (
    group_key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    started_at REAL,
    updated_at REAL,
    article_id INTEGER,
    model TEXT,
    models TEXT
);
"""


@dataclass
class RawItem:
    id: int
    source_id: str
    url: str
    title: str
    published_at: float
    content: str
    og_image: str | None


@dataclass
class Article:
    id: int
    slug: str
    title_pl: str
    lead_pl: str
    body_pl: str
    category: str
    sources: list
    bench: list | None
    promo: str | None
    embedding: list
    published_at: float = 0.0
    delta_of: int | None = None
    updated_at: float = 0.0
    image: str | None = None


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("embedding dimensions differ")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.execute("CREATE TABLE IF NOT EXISTS processed_raw (raw_id INTEGER PRIMARY KEY REFERENCES raw(id))")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(raw)")}
        if "summary_pl" not in columns:
            self.db.execute("ALTER TABLE raw ADD COLUMN summary_pl TEXT")
            self.db.commit()
        article_cols = {row[1] for row in self.db.execute("PRAGMA table_info(article)")}
        if "image" not in article_cols:
            self.db.execute("ALTER TABLE article ADD COLUMN image TEXT")
            self.db.commit()
        job_cols = {row[1] for row in self.db.execute("PRAGMA table_info(editor_job)")}
        if "model" not in job_cols:
            self.db.execute("ALTER TABLE editor_job ADD COLUMN model TEXT")
            self.db.commit()
        if "models" not in job_cols:
            self.db.execute("ALTER TABLE editor_job ADD COLUMN models TEXT")
            self.db.commit()

    def upsert_raw(self, source_id, url, title, published_at, content, og_image) -> int:
        cur = self.db.execute(
            "INSERT INTO raw (source_id, url, title, published_at, content, og_image) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(url) DO NOTHING",
            (source_id, url, title, published_at, content, og_image),
        )
        self.db.commit()
        if cur.lastrowid and cur.rowcount:
            return cur.lastrowid
        return self.db.execute("SELECT id FROM raw WHERE url = ?", (url,)).fetchone()["id"]

    def untriaged(self) -> list[RawItem]:
        rows = self.db.execute("SELECT * FROM raw WHERE triaged = 0 ORDER BY published_at").fetchall()
        return [RawItem(**{k: r[k] for k in RawItem.__dataclass_fields__}) for r in rows]

    def relevant_candidates(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM raw WHERE triaged = 1 AND relevant = 1 AND id NOT IN (SELECT raw_id FROM processed_raw) ORDER BY published_at DESC").fetchall()
        return [dict(row) for row in rows]

    def mark_processed(self, raw_id: int):
        self.db.execute("INSERT OR IGNORE INTO processed_raw (raw_id) VALUES (?)", (raw_id,))
        self.db.commit()

    def job(self, group_key: str):
        return self.db.execute("SELECT * FROM editor_job WHERE group_key = ?", (group_key,)).fetchone()

    def mark_running(self, group_key: str):
        now = time.time()
        self.db.execute(
            "INSERT INTO editor_job (group_key, state, started_at, updated_at, article_id, model) "
            "VALUES (?, 'running', ?, ?, NULL, NULL) ON CONFLICT(group_key) DO UPDATE SET "
            "state = 'running', started_at = excluded.started_at, updated_at = excluded.updated_at, article_id = NULL, model = NULL",
            (group_key, now, now),
        )
        self.db.commit()

    def mark_done(self, group_key: str, article_id: int, model: str | None = None, models: list[str] | None = None):
        now = time.time()
        self.db.execute(
            "INSERT INTO editor_job (group_key, state, started_at, updated_at, article_id, model, models) "
            "VALUES (?, 'done', ?, ?, ?, ?, ?) ON CONFLICT(group_key) DO UPDATE SET "
            "state = 'done', article_id = excluded.article_id, updated_at = excluded.updated_at, "
            "model = excluded.model, models = excluded.models",
            (group_key, now, now, article_id, model, json.dumps(models or ([model] if model else []))),
        )
        self.db.commit()

    def heartbeat(self, group_key: str):
        self.db.execute("UPDATE editor_job SET updated_at = ? WHERE group_key = ?", (time.time(), group_key))
        self.db.commit()

    def firecrawl_used_this_month(self) -> int:
        self.db.execute("CREATE TABLE IF NOT EXISTS firecrawl_usage (month TEXT PRIMARY KEY, credits INTEGER DEFAULT 0)")
        month = time.strftime("%Y-%m")
        row = self.db.execute("SELECT credits FROM firecrawl_usage WHERE month = ?", (month,)).fetchone()
        return row["credits"] if row else 0

    def firecrawl_add_credit(self):
        month = time.strftime("%Y-%m")
        self.db.execute(
            "INSERT INTO firecrawl_usage (month, credits) VALUES (?, 1) "
            "ON CONFLICT(month) DO UPDATE SET credits = credits + 1", (month,))
        self.db.commit()

    def sonar_used_cents_this_month(self) -> float:
        self.db.execute("CREATE TABLE IF NOT EXISTS sonar_usage (month TEXT PRIMARY KEY, cost_cents REAL DEFAULT 0)")
        row = self.db.execute("SELECT cost_cents FROM sonar_usage WHERE month = ?", (time.strftime("%Y-%m"),)).fetchone()
        return row["cost_cents"] if row else 0

    def sonar_add_cost(self, usd: float):
        self.db.execute("INSERT INTO sonar_usage (month, cost_cents) VALUES (?, ?) ON CONFLICT(month) DO UPDATE SET cost_cents = cost_cents + excluded.cost_cents", (time.strftime("%Y-%m"), usd * 100))
        self.db.commit()

    def add_research_engines(self, article_id: int, engines: list[str]):
        old = self.research_engines().get(article_id, [])
        merged = list(dict.fromkeys(old + engines))
        self.db.execute("INSERT INTO research_engines (article_id, engines) VALUES (?, ?) ON CONFLICT(article_id) DO UPDATE SET engines = excluded.engines", (article_id, json.dumps(merged)))
        self.db.commit()

    def research_engines(self) -> dict[int, list[str]]:
        return {r["article_id"]: json.loads(r["engines"]) for r in self.db.execute("SELECT article_id, engines FROM research_engines")}

    def article_models(self) -> dict[int, str]:
        """Który model napisał który artykuł (kredyt 'tłumaczył' na stronie)."""
        return {r["article_id"]: r["model"] for r in self.db.execute(
            "SELECT article_id, model FROM editor_job WHERE article_id IS NOT NULL AND model IS NOT NULL")}

    def article_model_chain(self) -> dict[int, list[str]]:
        """Wszystkie modele użyte przy artykule (korekta, eskalacja) - stopka redakcyjna."""
        out = {}
        for row in self.db.execute("SELECT article_id, model, models FROM editor_job WHERE article_id IS NOT NULL"):
            chain = json.loads(row["models"]) if row["models"] else ([row["model"]] if row["model"] else [])
            if chain:
                out[row["article_id"]] = chain
        return out

    def mark_failed(self, group_key: str):
        now = time.time()
        self.db.execute(
            "INSERT INTO editor_job (group_key, state, started_at, updated_at) "
            "VALUES (?, 'failed', ?, ?) ON CONFLICT(group_key) DO UPDATE SET "
            "state = 'failed', updated_at = excluded.updated_at",
            (group_key, now, now),
        )
        self.db.commit()

    def set_tool_versions(self, versions: dict[str, str]):
        now = time.time()
        for repo, tag in versions.items():
            self.db.execute(
                "INSERT INTO tool_version (repo, tag, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(repo) DO UPDATE SET tag = excluded.tag, fetched_at = excluded.fetched_at",
                (repo, tag, now),
            )
        self.db.commit()

    def tool_versions(self) -> dict[str, str]:
        return {r["repo"]: r["tag"] for r in self.db.execute("SELECT repo, tag FROM tool_version")}

    def mark_triaged(self, raw_id: int, relevant: bool, category: str | None):
        self.db.execute(
            "UPDATE raw SET triaged = 1, relevant = ?, category = ? WHERE id = ?",
            (int(relevant), category, raw_id),
        )
        self.db.commit()

    def add_article(self, slug, title_pl, lead_pl, body_pl, category, sources, bench,
                    promo, embedding, published_at, delta_of, image=None) -> int:
        cur = self.db.execute(
            "INSERT INTO article (slug, title_pl, lead_pl, body_pl, category, sources_json, "
            "bench_json, promo, embedding_json, image, published_at, delta_of, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, title_pl, lead_pl, body_pl, category, json.dumps(sources, ensure_ascii=False),
             json.dumps(bench, ensure_ascii=False) if bench is not None else None,
             promo, json.dumps(embedding), image, published_at, delta_of, published_at),
        )
        self.db.commit()
        return cur.lastrowid

    def update_article(self, article_id: int, **fields):
        allowed = {"title_pl", "lead_pl", "body_pl", "category", "promo", "delta_of", "image"}
        json_fields = {"sources": "sources_json", "bench": "bench_json", "embedding": "embedding_json"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
            elif k in json_fields:
                sets.append(f"{json_fields[k]} = ?")
                vals.append(json.dumps(v, ensure_ascii=False))
        if not sets:
            return
        sets.append("updated_at = ?")
        vals.append(time.time())
        vals.append(article_id)
        self.db.execute(f"UPDATE article SET {', '.join(sets)} WHERE id = ?", vals)
        self.db.commit()

    def _row_to_article(self, r) -> Article:
        d = dict(r)
        return Article(
            id=d["id"], slug=d["slug"], title_pl=d["title_pl"], lead_pl=d["lead_pl"],
            body_pl=d["body_pl"], category=d["category"],
            sources=json.loads(d["sources_json"] or "[]"),
            bench=json.loads(d["bench_json"]) if d["bench_json"] else None,
            promo=d["promo"], embedding=json.loads(d["embedding_json"] or "[]"),
            image=d.get("image"), published_at=d["published_at"],
            delta_of=d["delta_of"], updated_at=d["updated_at"],
        )

    def articles_since(self, ts: float) -> list[Article]:
        rows = self.db.execute(
            "SELECT * FROM article WHERE published_at >= ? ORDER BY published_at DESC", (ts,)
        ).fetchall()
        return [self._row_to_article(r) for r in rows]

    def article_by_slug(self, slug: str) -> Article | None:
        r = self.db.execute("SELECT * FROM article WHERE slug = ?", (slug,)).fetchone()
        return self._row_to_article(r) if r else None

    def article_by_id(self, article_id: int) -> Article:
        row = self.db.execute("SELECT * FROM article WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            raise ValueError("article does not exist")
        return self._row_to_article(row)

    def all_embeddings(self, days: int) -> list[tuple[int, str, list]]:
        cutoff = time.time() - days * 86400
        rows = self.db.execute(
            "SELECT id, title_pl, embedding_json FROM article WHERE published_at >= ?", (cutoff,)
        ).fetchall()
        return [(r["id"], r["title_pl"], json.loads(r["embedding_json"] or "[]")) for r in rows]

    def price_baseline(self, vendor: str, model: str) -> dict | None:
        r = self.db.execute(
            "SELECT * FROM price WHERE vendor = ? AND model = ?", (vendor, model)
        ).fetchone()
        return dict(r) if r else None

    def set_price(self, vendor, model, price_in, price_out, currency, seen_at):
        old = self.price_baseline(vendor, model)
        if old and (old["price_in"] != price_in or old["price_out"] != price_out):
            self.db.execute(
                "INSERT INTO price_history (vendor, model, old_in, old_out, new_in, new_out, seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (vendor, model, old["price_in"], old["price_out"], price_in, price_out, seen_at),
            )
        self.db.execute(
            "INSERT INTO price (vendor, model, price_in, price_out, currency, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(vendor, model) DO UPDATE SET "
            "price_in = excluded.price_in, price_out = excluded.price_out, "
            "currency = excluded.currency, seen_at = excluded.seen_at",
            (vendor, model, price_in, price_out, currency, seen_at),
        )
        self.db.commit()

    def price_changes(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM price_history ORDER BY seen_at DESC").fetchall()
        return [dict(r) for r in rows]

    def log_run(self, started_at, finished_at, new_count, errors) -> int:
        cur = self.db.execute(
            "INSERT INTO run_log (started_at, finished_at, new_count, errors_json) VALUES (?, ?, ?, ?)",
            (started_at, finished_at, new_count, json.dumps(errors, ensure_ascii=False)),
        )
        self.db.commit()
        return cur.lastrowid

    def runs_today(self, day_start_ts: float) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM run_log WHERE started_at >= ?", (day_start_ts,)
        ).fetchone()[0]