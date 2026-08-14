from __future__ import annotations

from typing import Any

from .generator import Generator
from .generator import fallback_answer
from .models import Chunk


class RagAgent:
    """回答质量自检与有限次重试。"""

    def __init__(self, generator: Generator, max_retries: int = 2):
        self.generator = generator
        self.max_retries = max_retries

    def answer(
        self,
        query: str,
        chunks: list[Chunk],
        history: list[dict[str, str]],
    ) -> tuple[str, int, str]:
        messages = self.generator._build_qa_messages(query, chunks, history)
        answer = self.generator.generate(messages)
        reason = self._check(answer, chunks)
        attempts = 1

        while not reason and attempts <= self.max_retries:
            messages.append({"role": "assistant", "content": answer})
            messages.append(
                {
                    "role": "user",
                    "content": "请重新回答：必须严格基于资料，使用 [1]、[2] 标注引用，如果资料不足请明确说明。",
                }
            )
            answer = self.generator.generate(messages)
            attempts += 1
            reason = self._check(answer, chunks)

        if chunks and ("无法回答" in answer or "没有找到" in answer or "没有检索到" in answer):
            return fallback_answer(query, chunks), attempts, "fallback_to_sources"
        return answer, attempts, reason

    @staticmethod
    def _check(answer: str, chunks: list[Chunk]) -> str:
        if not chunks:
            return "no_chunks"
        if not answer or not answer.strip():
            return "empty_answer"
        if "[1]" not in answer:
            return "missing_citation"
        if "没有检索到相关资料" in answer or "没有找到与这个问题相关的资料" in answer:
            return "no_grounding"
        return "ok"
