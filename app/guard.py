from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .prompts import PromptManager


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


class Guard:
    def __init__(self, prompts: PromptManager | None = None):
        self.prompts = prompts or PromptManager()
        self.blocked_patterns = [
            r"(ignore|forget)\s+(all|previous|system)",
            r"reveal\s+(your|the)\s+system",
            r"system\s*:\s*",
            r"\[\s*system\s*\]",
            r"<\|im_start\|>",
            r"</?system>",
        ]
        self.pii_patterns = [
            r"\b\d{18}\b",
            r"\b\d{17}[\dXx]\b",
            r"\b1[3-9]\d{9}\b",
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        ]

    def validate_input(self, text: str, max_length: int | None = None) -> GuardResult:
        max_length = max_length or settings.guard_max_input_length
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return GuardResult(False, "问题不能为空。")
        if len(text) > max_length:
            return GuardResult(False, f"问题长度不能超过 {max_length} 个字符。")
        lower = text.lower()
        if any(re.search(pattern, lower) for pattern in self.blocked_patterns):
            return GuardResult(False, "请求被安全策略拦截。")
        return GuardResult(True)

    def validate_output(self, query: str, answer: str, max_length: int | None = None) -> GuardResult:
        max_length = max_length or settings.guard_max_output_length
        if not answer.strip():
            return GuardResult(False, "生成结果为空。")
        if len(answer) > max_length:
            return GuardResult(False, "生成结果超过安全长度限制。")
        lower = answer.lower()
        if any(re.search(pattern, lower) for pattern in self.blocked_patterns):
            return GuardResult(False, "生成结果被安全策略拦截。")
        return GuardResult(True)

    def validate_upload(
        self,
        filename: str,
        content_type: str | None,
        size: int,
    ) -> GuardResult:
        suffix = Path(filename or "").suffix.lower()
        allowed = {".txt", ".md", ".markdown", ".docx", ".pdf", ".csv", ".json", ".log"}
        if suffix not in allowed:
            return GuardResult(False, f"不支持的文件类型：{suffix or filename}")
        if size <= 0:
            return GuardResult(False, "文件内容为空。")
        if size > settings.guard_upload_max_bytes:
            return GuardResult(False, f"文件大小不能超过 {settings.guard_upload_max_bytes // (1024 * 1024)}MB。")
        return GuardResult(True)

    def prompt_version(self) -> dict[str, Any]:
        return {
            "answer_guard": self.prompts.list_versions("answer_guard"),
            "qa": self.prompts.list_versions("qa"),
            "query_rewrite": self.prompts.list_versions("query_rewrite"),
        }
