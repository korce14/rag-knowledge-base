from __future__ import annotations

import re
from difflib import SequenceMatcher


def detect_and_decode(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\ufeff", "").replace("\u3000", " ")
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def deduplicate_chunks(chunks: list[str], threshold: float = 0.95) -> list[str]:
    result: list[str] = []
    for chunk in chunks:
        chunk = clean_text(chunk)
        if not chunk:
            continue
        if any(SequenceMatcher(None, chunk, existing).ratio() >= threshold for existing in result[-50:]):
            continue
        result.append(chunk)
    return result

