from __future__ import annotations

import httpx

from .config import settings
from .models import ScoredChunk
from .resilience import CircuitBreaker
from .text import tokenize


class Reranker:
    """可选的重排器，优先调用 API，未配置时退回词汇相关度排序。"""

    def __init__(self, breaker: CircuitBreaker | None = None):
        self.breaker = breaker or CircuitBreaker("rerank")
        self._client = httpx.Client(timeout=30.0)

    @property
    def available(self) -> bool:
        return settings.rerank_enabled

    def rerank(self, query: str, hits: list[ScoredChunk]) -> list[ScoredChunk]:
        if not hits:
            return []
        if self.available:
            try:
                return self.breaker.call(self._api_rerank, query, hits)
            except Exception:
                pass
        return self._lexical_rerank(query, hits)

    def _api_rerank(self, query: str, hits: list[ScoredChunk]) -> list[ScoredChunk]:
        documents = [hit.chunk.text for hit in hits]
        response = self._client.post(
            f"{settings.rerank_base_url.rstrip('/')}/rerank",
            headers={"Authorization": f"Bearer {settings.rerank_api_key}"},
            json={
                "model": settings.rerank_model,
                "query": query,
                "documents": documents,
                "top_n": len(hits),
            },
        )
        response.raise_for_status()
        payload = response.json()
        scores: dict[int, float] = {
            item["index"]: float(item.get("relevance_score", 0.0))
            for item in payload.get("results", [])
        }
        reranked = sorted(hits, key=lambda hit: scores.get(hits.index(hit), 0.0), reverse=True)
        for hit in reranked:
            hit.score = scores.get(hits.index(hit), hit.score)
        return reranked

    def _lexical_rerank(self, query: str, hits: list[ScoredChunk]) -> list[ScoredChunk]:
        query_terms = set(tokenize(query))
        for hit in hits:
            terms = set(tokenize(hit.chunk.text))
            overlap = len(query_terms.intersection(terms)) / max(len(query_terms), 1)
            hit.score = hit.score + overlap * 0.25
        return sorted(hits, key=lambda hit: hit.score, reverse=True)
