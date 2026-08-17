from __future__ import annotations

from collections import defaultdict

from .config import settings
from .embedder import Embedder
from .models import Chunk, ScoredChunk
from .text import BM25
from .vector_store import VectorStore
from typing import Any

from .text import tokenize


class Retriever:
    def __init__(self, chunks: list[Chunk], kb_id: str, vector_store: VectorStore, embedder: Embedder, persisted_tokens: dict[str, dict[str, Any]] | None = None):
        self.chunks = chunks
        self.kb_id = kb_id
        self.vector_store = vector_store
        self.embedder = embedder
        self._chunk_by_id = {chunk.id: chunk for chunk in chunks}
        self.bm25 = BM25()
        if persisted_tokens:
            ordered = []
            for chunk in chunks:
                entry = persisted_tokens.get(chunk.id)
                ordered.append(entry["tokens"] if entry else tokenize(chunk.text))
            if ordered:
                self.bm25.fit_token_lists(ordered)
        if not self.bm25.corpus and chunks:
            self.bm25.fit([chunk.text for chunk in chunks])


    def search(
        self,
        query: str,
        top_k: int = settings.top_k,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ScoredChunk]:
        allowed_ids = self._filtered_ids(document_id=document_id, tags=tags)
        allowed_chunks = [chunk for chunk in self.chunks if chunk.id in allowed_ids]

        dense_hits: dict[str, float] = {}
        if self.embedder.available and allowed_chunks:
            try:
                query_vector = self.embedder.embed_query(query)
                allowed_doc_ids = {chunk.document_id for chunk in allowed_chunks}
                dense_hits = dict(
                    self.vector_store.search(
                        self.kb_id,
                        query_vector,
                        top_k=max(top_k * 3, 10),
                        allowed_ids=set(allowed_ids),
                        document_ids=allowed_doc_ids,
                        tags=tags,
                    )
                )
            except Exception:
                dense_hits = {}

        sparse_hits: dict[str, float] = {}
        if allowed_chunks:
            all_scores = self.bm25.scores(query)
            score_by_id = {chunk.id: all_scores[index] for index, chunk in enumerate(self.chunks)}
            for chunk, score in sorted(score_by_id.items(), key=lambda item: item[1], reverse=True):
                if chunk not in allowed_ids or score <= 0:
                    continue
                sparse_hits[chunk] = score
                if len(sparse_hits) >= top_k * 3:
                    break

        fused = self._rrf_fuse(dense_hits, sparse_hits, k=settings.rrf_k)
        results: list[ScoredChunk] = []
        for chunk_id, score in sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_k]:
            chunk = self._chunk_by_id.get(chunk_id)
            if chunk is None:
                continue
            results.append(
                ScoredChunk(
                    chunk=chunk,
                    score=score,
                    dense_score=dense_hits.get(chunk_id),
                    bm25_score=sparse_hits.get(chunk_id),
                )
            )
        return results

    def _filtered_ids(self, document_id: str | None, tags: list[str] | None) -> set[str]:
        allowed = {chunk.id for chunk in self.chunks}
        if document_id:
            allowed = {chunk.id for chunk in self.chunks if chunk.document_id == document_id}
        if tags:
            wanted = set(tags)
            allowed = {chunk.id for chunk in self.chunks if chunk.id in allowed and wanted.intersection(chunk.tags)}
        return allowed

    @staticmethod
    def _rrf_fuse(dense_hits: dict[str, float], sparse_hits: dict[str, float], k: int = 60) -> dict[str, float]:
        fused: dict[str, float] = defaultdict(float)
        for rank, chunk_id in enumerate(sorted(dense_hits, key=lambda cid: dense_hits[cid], reverse=True)):
            fused[chunk_id] += 1 / (k + rank + 1)
        for rank, chunk_id in enumerate(sorted(sparse_hits, key=lambda cid: sparse_hits[cid], reverse=True)):
            fused[chunk_id] += 1 / (k + rank + 1)
        return fused
