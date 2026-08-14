from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import ScoredChunk
from .storage import Database


class FeedbackWeights:
    def __init__(self, db: Database):
        self.db = db

    def apply(self, kb_id: str, hits: list[ScoredChunk]) -> list[ScoredChunk]:
        weights = self.weights(kb_id)
        for hit in hits:
            weight = weights.get(hit.chunk.document_id, 1.0)
            hit.score = hit.score * weight
        return sorted(hits, key=lambda item: item.score, reverse=True)

    def weights(self, kb_id: str) -> dict[str, float]:
        rows = self.db.list_feedback_signals(kb_id)
        signals: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            signals[row["document_id"]].append(row["rating_value"])

        weights: dict[str, float] = {}
        for document_id, ratings in signals.items():
            positive = sum(1 for value in ratings if value > 0)
            negative = sum(1 for value in ratings if value < 0)
            # 温和调整，避免少数反馈让排序发生剧烈跳变。
            weights[document_id] = round(1.0 + positive * 0.05 - negative * 0.08, 3)
        return weights


def record_feedback(
    db: Database,
    session_id: str,
    question: str,
    answer: str,
    rating: str,
    kb_id: str | None = None,
    document_ids: list[str] | None = None,
) -> None:
    rating_value = _rating_value(rating)
    document_ids = document_ids or [None]
    for document_id in document_ids:
        db.add_feedback_signal(
            session_id=session_id,
            question=question,
            answer=answer,
            rating=rating,
            rating_value=rating_value,
            kb_id=kb_id,
            document_id=document_id,
        )


def _rating_value(rating: str) -> int:
    rating = rating.lower()
    if rating in {"good", "positive", "up", "1", "like"}:
        return 1
    if rating in {"bad", "negative", "down", "-1", "dislike"}:
        return -1
    return 0
