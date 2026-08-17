from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PipelineContext:
    actor: Any
    kb_id: str
    question: str
    session_id: str = "default"
    top_k: int = 5
    document_id: str | None = None
    tags: list[str] | None = None
    result: dict[str, Any] | None = None
    state: dict[str, Any] = field(default_factory=dict)


class PipelineStep:
    name = "step"

    def run(self, context: PipelineContext) -> bool:
        """Return True when pipeline should continue, False to stop."""
        return True


class GuardStep(PipelineStep):
    name = "guard"

    def __init__(self, validate_input: Callable[[str], Any]):
        self.validate_input = validate_input

    def run(self, context: PipelineContext) -> bool:
        checked = self.validate_input(context.question)
        if not checked.ok:
            context.result = {
                "answer": checked.reason,
                "sources": [],
                "route": "blocked",
                "elapsed_ms": 0,
            }
            return False
        return True


class CacheStep(PipelineStep):
    name = "cache"

    def __init__(self, read: Callable[[str], dict[str, Any] | None]):
        self.read = read

    def run(self, context: PipelineContext) -> bool:
        cache_key = context.state["cache_key"]
        cached = self.read(cache_key)
        if cached is not None:
            context.result = {
                "answer": cached["answer"],
                "sources": cached["sources"],
                "route": "cached",
                "elapsed_ms": 0,
            }
            return False
        return True


class RetrieveStep(PipelineStep):
    name = "retrieve"

    def __init__(
        self,
        retrieve: Callable[..., list[Any]],
        format_sources: Callable[[list[Any]], list[dict[str, Any]]],
    ):
        self.retrieve = retrieve
        self.format_sources = format_sources

    def run(self, context: PipelineContext) -> bool:
        hits = self.retrieve(
            context.actor,
            context.kb_id,
            context.question,
            top_k=context.top_k,
            document_id=context.document_id,
            tags=context.tags,
        )
        context.state["hits"] = hits
        context.state["sources"] = self.format_sources(hits)
        context.state["chunks"] = [hit.chunk for hit in hits]
        return True


class AgentStep(PipelineStep):
    name = "agent"

    def __init__(
        self,
        answer: Callable[..., tuple[str, int, str]],
        fallback: Callable[..., str],
        validate_output: Callable[[str, str], Any],
        record: Callable[..., None],
        cache_write: Callable[..., None],
    ):
        self.answer = answer
        self.fallback = fallback
        self.validate_output = validate_output
        self.record = record
        self.cache_write = cache_write

    def run(self, context: PipelineContext) -> bool:
        chunks = context.state.get("chunks", [])
        question = context.question
        session_id = context.session_id
        sources = context.state.get("sources", [])
        try:
            answer_text, attempts, reason = self.answer(question, chunks, self.record("history", session_id, context.actor.id))
        except Exception:
            answer_text = self.fallback(question, chunks)
            attempts = 0
            reason = "fallback"

        checked = self.validate_output(question, answer_text)
        if not checked.ok:
            answer_text = "回答被安全策略拦截，请尝试更具体或合规的问题。"

        self.record("messages", session_id, question, answer_text, context.actor.id)
        self.cache_write(context.state["cache_key"], answer_text, sources)
        context.result = {
            "answer": answer_text,
            "sources": sources,
            "route": "generated",
            "rewritten_query": question,
            "elapsed_ms": 0,
            "attempts": attempts,
            "check_reason": reason,
        }
        return True


class PipelineScheduler:
    def __init__(self, steps: list[PipelineStep]):
        self.steps = steps

    def run(self, context: PipelineContext) -> dict[str, Any]:
        for step in self.steps:
            if not step.run(context):
                return context.result or {}
        return context.result or {}

