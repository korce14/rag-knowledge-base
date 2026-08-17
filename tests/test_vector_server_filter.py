from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.config import settings
from app.vector_store import QdrantVectorStore


class FakeQdrantClient:
    def __init__(self) -> None:
        self.last_limit: int | None = None
        self.last_filter = None

    def collection_exists(self, name: str) -> bool:
        return True

    def query_points(self, **kwargs):
        self.last_limit = kwargs.get("limit")
        self.last_filter = kwargs.get("query_filter")
        return SimpleNamespace(
            points=[
                SimpleNamespace(payload={"chunk_id": "c1"}, score=0.9),
                SimpleNamespace(payload={"chunk_id": "c2"}, score=0.8),
            ]
        )


def test_qdrant_search_pushes_chunk_filter_to_server(monkeypatch) -> None:
    monkeypatch.setattr(settings, "qdrant_url", "http://127.0.0.1:6333")
    store = QdrantVectorStore()
    client = FakeQdrantClient()
    monkeypatch.setattr(store, "_get_client", lambda: client)

    hits = store.search("kb", np.array([1.0], dtype="float32"), top_k=3, allowed_ids={"c1", "c2"}, document_ids={"d1"})

    assert hits == [("c1", 0.9), ("c2", 0.8)]
    assert client.last_limit == 3
    assert client.last_filter is not None
    keys = {condition.key for condition in client.last_filter.must}
    assert "chunk_id" in keys


def test_qdrant_search_returns_empty_for_empty_allowed_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "qdrant_url", "http://127.0.0.1:6333")
    store = QdrantVectorStore()
    client = FakeQdrantClient()
    monkeypatch.setattr(store, "_get_client", lambda: client)

    assert store.search("kb", np.array([1.0], dtype="float32"), top_k=3, allowed_ids=set()) == []
    assert client.last_limit is None
