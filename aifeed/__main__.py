"""Command-line entry point for one aggregation pass."""
import json
import os
import time
from pathlib import Path

from .config import Settings
from .pipeline import run


def load_env(path):
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main(retry_interval=1800, max_retries=4):
    """Ponawiaj co retry_interval (30 min), dopóki są zaległości; max_retries chroni przed pętlą bez końca."""
    load_env(os.environ.get("AIFEED_ENV", ".env"))
    settings = Settings.from_env()
    for attempt in range(max_retries + 1):
        try:
            result = run(settings)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if not result.get("backlog"):
                break
        except Exception as exc:
            # Awaria transportu (restart gatewaya, blip DNS) nie może ominąć ponowień.
            print(json.dumps({"error": str(exc)[:200]}), flush=True)
            result = {"backlog": 1}
        if attempt < max_retries:
            time.sleep(retry_interval)


if __name__ == "__main__":
    main()
