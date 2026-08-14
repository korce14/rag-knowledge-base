from __future__ import annotations

import asyncio
from typing import AsyncIterator

from openai import AsyncOpenAI, OpenAI

from .config import settings
from .models import Chunk
from .prompts import PromptManager
from .resilience import CircuitBreaker


def build_context_prompt(query: str, chunks: list[Chunk]) -> str:
    if not chunks:
        return "没有检索到相关资料。"
    numbered = []
    for index, chunk in enumerate(chunks, start=1):
        numbered.append(f"[{index}] 来源：{chunk.document_name}\n{chunk.text}")
    return "\n\n".join(numbered)


def build_messages(query: str, chunks: list[Chunk], history: list[dict[str, str]]) -> list[dict[str, str]]:
    prompts = PromptManager()
    rendered = prompts.render("qa", {"query": query, "context": build_context_prompt(query, chunks)})
    messages: list[dict[str, str]] = [{"role": "system", "content": rendered["system"]}]
    messages.extend(history[-8:])
    messages.append({"role": "user", "content": rendered["user"]})
    return messages


def fallback_answer(query: str, chunks: list[Chunk]) -> str:
    if not chunks:
        return "当前知识库中没有找到与这个问题相关的资料。"
    lines = ["当前没有配置生成模型，因此以下回答直接来自检索到的资料原文：", ""]
    for index, chunk in enumerate(chunks, start=1):
        lines.append(f"[{index}] {chunk.document_name}\n{chunk.text.strip()}")
    return "\n\n".join(lines)


class Generator:
    def __init__(self, prompts: PromptManager | None = None, breaker: CircuitBreaker | None = None):
        self.prompts = prompts or PromptManager()
        self.breaker = breaker or CircuitBreaker("generation")
        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None

    @property
    def available(self) -> bool:
        return settings.generation_enabled

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=settings.generation_api_key,
                base_url=settings.generation_base_url,
                timeout=60.0,
            )
        return self._client

    def _get_async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=settings.generation_api_key,
                base_url=settings.generation_base_url,
                timeout=60.0,
            )
        return self._async_client

    def _build_qa_messages(
        self,
        query: str,
        chunks: list[Chunk],
        history: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        rendered = self.prompts.render("qa", {"query": query, "context": build_context_prompt(query, chunks)})
        messages: list[dict[str, str]] = [{"role": "system", "content": rendered["system"]}]
        messages.extend(history[-8:])
        messages.append({"role": "user", "content": rendered["user"]})
        return messages

    def generate(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        response = self.breaker.call(
            lambda: self._get_client().chat.completions.create(
                model=settings.generation_model,
                messages=messages,
                temperature=temperature,
            )
        )
        return response.choices[0].message.content or ""

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        async def create_stream():
            return await self._get_async_client().chat.completions.create(
                model=settings.generation_model,
                messages=messages,
                temperature=0.2,
                stream=True,
            )

        stream = await self.breaker.call_async(create_stream)
        async for event in stream:
            if event.choices and event.choices[0].delta.content:
                yield event.choices[0].delta.content

    def rewrite_query(self, query: str) -> str:
        """调用生成模型补齐指代、拆分意图，失败时返回原问题。"""
        if not self.available:
            return query
        try:
            rendered = self.prompts.render("query_rewrite", {"query": query})
            messages = [
                {"role": "system", "content": rendered["system"]},
                {"role": "user", "content": rendered["user"]},
            ]
            rewritten = self.generate(messages, temperature=0.0).strip() or query
            if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in rewritten):
                return query
            return rewritten
        except Exception:
            return query


async def stream_text(iterator: AsyncIterator[str]) -> AsyncIterator[str]:
    async for token in iterator:
        yield token
    await asyncio.sleep(0)

