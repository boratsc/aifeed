"""Embedding client (ollama /api/embed)."""
import httpx

from .config import Settings


def vector(settings: Settings, text: str) -> list[float]:
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{settings.embed_url}/api/embed",
            json={"model": settings.embed_model, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]