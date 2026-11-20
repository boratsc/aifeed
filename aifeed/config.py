"""Settings from environment. Real values live in .env, never in the repo."""
import os
from dataclasses import dataclass


@dataclass
class Settings:
    gateway_url: str = "https://localhost:8317/v1"
    gateway_key: str = ""
    triage_model: str = "deepseek-flash-direct"
    editor_model: str = "digest-editor"
    embed_url: str = "http://localhost:11434"
    embed_model: str = "qwen3-embedding:0.6b"
    db_path: str = "data/aifeed.db"
    out_dir: str = "data/out"
    img_dir: str = "data/out/img"
    bercik_url: str = ""
    bercik_token: str = ""
    dedup_days: int = 14
    dedup_threshold: float = 0.86
    backfill_days: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            gateway_url=env.get("GATEWAY_URL", cls.gateway_url),
            gateway_key=env.get("GATEWAY_KEY", ""),
            triage_model=env.get("TRIAGE_MODEL", cls.triage_model),
            editor_model=env.get("EDITOR_MODEL", cls.editor_model),
            embed_url=env.get("EMBED_URL", cls.embed_url),
            embed_model=env.get("EMBED_MODEL", cls.embed_model),
            db_path=env.get("DB_PATH", cls.db_path),
            out_dir=env.get("OUT_DIR", cls.out_dir),
            img_dir=env.get("IMG_DIR", cls.img_dir),
            bercik_url=env.get("BERCIK_URL", ""),
            bercik_token=env.get("BERCIK_TOKEN", ""),
            dedup_days=int(env.get("DEDUP_DAYS", cls.dedup_days)),
            dedup_threshold=float(env.get("DEDUP_THRESHOLD", cls.dedup_threshold)),
            backfill_days=int(env.get("BACKFILL_DAYS", cls.backfill_days)),
        )