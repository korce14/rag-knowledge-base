from __future__ import annotations

import re
from collections import Counter
from math import log
from .cleaner import clean_text

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """中文优先使用 jieba，英文和数字按单词处理。"""
    tokens: list[str] = []
    for part in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", text):
        if _CJK_RE.search(part):
            tokens.extend(_cut_chinese(part))
        else:
            tokens.append(part)
    return tokens


def _cut_chinese(text: str) -> list[str]:
    try:
        import jieba

        return [token for token in jieba.cut(text) if token.strip()]
    except Exception:
        return list(text)



def chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if not buffer:
            buffer = paragraph
        elif len(buffer) + len(paragraph) + 2 <= chunk_size:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            chunks.extend(_split_long(buffer, chunk_size, overlap))
            buffer = paragraph
    if buffer:
        chunks.extend(_split_long(buffer, chunk_size, overlap))
    return [chunk for chunk in chunks if chunk.strip()]


def _split_long(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


class BM25:
    """轻量 BM25 实现，避免额外的搜索索引依赖。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus: list[list[str]] = []
        self.doc_len: list[int] = []
        self.doc_freq: Counter[str] = Counter()
        self.avgdl = 0.0

    def fit(self, documents: list[str]) -> None:
        self.corpus = [tokenize(doc) for doc in documents]
        self.doc_len = [len(tokens) for tokens in self.corpus]
        self.avgdl = sum(self.doc_len) / max(len(self.doc_len), 1)
        self.doc_freq = Counter()
        for tokens in self.corpus:
            for term in set(tokens):
                self.doc_freq[term] += 1

    def scores(self, query: str) -> list[float]:
        if not self.corpus:
            return []
        query_terms = tokenize(query)
        n = len(self.corpus)
        scores = [0.0] * n
        for term in query_terms:
            df = self.doc_freq.get(term, 0)
            idf = log(1 + (n - df + 0.5) / (df + 0.5))
            for idx, tokens in enumerate(self.corpus):
                tf = tokens.count(term)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[idx] / max(self.avgdl, 1e-9))
                scores[idx] += idf * (tf * (self.k1 + 1)) / denom
        return scores
