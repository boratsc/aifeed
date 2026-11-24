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
    result = run(settings)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    retries = 0
    while result.get("backlog") and retries < max_retries:
        retries += 1
        time.sleep(retry_interval)
        result = run(settings)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
