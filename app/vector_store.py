from __future__ import annotations

import re
from typing import Any

import numpy as np
import uuid

from .config import settings


def _collection_name(kb_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", kb_id).strip("_")
    return f"{settings.qdrant_collection_prefix}_{safe}"


class QdrantVectorStore:
    """Qdrant vector store adapter.

    When Qdrant is not configured, vectors are not persisted locally and
    dense retrieval degrades to empty results. BM25 keeps the system usable.
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    @property
    def enabled(self) -> bool:
        return settings.qdrant_enabled

    def _get_client(self) -> Any:
        if not self.enabled:
            raise RuntimeError("Qdrant 未配置")
        if self._client is None:
            from qdrant_client import QdrantClient

            client_kwargs: dict[str, Any] = {"url": settings.qdrant_url}
            if settings.qdrant_api_key:
                client_kwargs["api_key"] = settings.qdrant_api_key
            if settings.qdrant_prefer_grpc:
                client_kwargs["prefer_grpc"] = True
            self._client = QdrantClient(**client_kwargs)
        return self._client

    def _ensure_collection(self, kb_id: str, dimension: int) -> None:
        from qdrant_client import models

        name = _collection_name(kb_id)
        client = self._get_client()
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            )

    def upsert(
        self,
        kb_id: str,
        ids: list[str],
        doc_ids: list[str],
        vectors: np.ndarray,
        payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        if not ids or not self.enabled:
            return
        from qdrant_client import models

        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        if vectors.size == 0:
            return

        self._ensure_collection(kb_id, vectors.shape[1])
        points: list[Any] = []
        for index, chunk_id in enumerate(ids):
            payload = {
                "kb_id": kb_id,
                "document_id": doc_ids[index],
                "chunk_id": chunk_id,
            }
            if payloads and index < len(payloads):
                payload.update(payloads[index])
            point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, chunk_id))
            points.append(models.PointStruct(id=point_id, vector=vectors[index].tolist(), payload=payload))
        self._get_client().upsert(collection_name=_collection_name(kb_id), points=points, wait=True)

    def replace_document(
        self,
        kb_id: str,
        document_id: str,
        ids: list[str],
        vectors: np.ndarray,
        payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self.delete_document(kb_id, document_id)
        self.upsert(kb_id, ids, [document_id] * len(ids), vectors, payloads)

    def delete_document(self, kb_id: str, document_id: str) -> None:
        if not self.enabled:
            return
        from qdrant_client import models

        client = self._get_client()
        name = _collection_name(kb_id)
        if not client.collection_exists(name):
            return
        client.delete(
            collection_name=name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                )
            ),
            wait=True,
        )

    def delete_kb(self, kb_id: str) -> None:
        if not self.enabled:
            return
        name = _collection_name(kb_id)
        client = self._get_client()
        if client.collection_exists(name):
            client.delete_collection(name)

    def search(
        self,
        kb_id: str,
        query_vector: np.ndarray,
        top_k: int = 10,
        allowed_ids: set[str] | None = None,
        document_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        if not self.enabled:
            return []
        try:
            client = self._get_client()
            from qdrant_client import models
            name = _collection_name(kb_id)
            if not client.collection_exists(name):
                return []

            search_limit = max(top_k, 1)
            if allowed_ids is not None:
                search_limit = max(search_limit * 4, 20)

            query_filter = None
            if document_ids is not None:
                query_filter = models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchAny(any=list(document_ids)))]
                )
            result = client.query_points(
                collection_name=name,
                query=np.asarray(query_vector, dtype=np.float32).reshape(1, -1).tolist()[0],
                query_filter=query_filter,
                limit=search_limit,
                with_payload=True,
            ).points

            hits: list[tuple[str, float]] = []
            for point in result:
                chunk_id = str(point.payload.get("chunk_id", point.id))
                if allowed_ids is not None and chunk_id not in allowed_ids:
                    continue
                hits.append((chunk_id, float(point.score)))
                if len(hits) >= top_k:
                    break
            return hits
        except Exception:
            return []


VectorStore = QdrantVectorStore



