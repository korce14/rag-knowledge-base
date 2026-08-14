from __future__ import annotations

import numpy as np
from openai import OpenAI

from .config import settings
from .resilience import CircuitBreaker


class EmbeddingUnavailable(RuntimeError):
    pass


class Embedder:
    """OpenAI 兼容嵌入客户端，可接 BGE-M3 等模型。"""

    def __init__(self, breaker: CircuitBreaker | None = None):
        self.breaker = breaker or CircuitBreaker("embedding")
        self._client: OpenAI | None = None

    @property
    def available(self) -> bool:
        return settings.dense_enabled

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_base_url,
                timeout=60.0,
            )
        return self._client

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, settings.embedding_dim), dtype=np.float32)
        if not self.available:
            raise EmbeddingUnavailable("未配置嵌入模型 API Key 或 Qdrant")
        vectors: list[list[float]] = []
        batch_size = 32

        def call_api(batch: list[str]) -> list[list[float]]:
            response = self._get_client().embeddings.create(
                model=settings.embedding_model,
                input=batch,
            )
            return [item.embedding for item in response.data]

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(self.breaker.call(call_api, batch))
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]
