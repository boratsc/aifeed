"""Command-line entry point for one aggregation pass."""
import json
from pathlib import Path

from .config import Settings
from .pipeline import run


def load_env(path):
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            import os
            os.environ.setdefault(key.strip(), value.strip())


def main():
    load_env(".env")
    result = run(Settings.from_env())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
