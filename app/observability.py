from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any


class StructuredLogger:
    def __init__(self, name: str = "rag"):
        self.logger = logging.getLogger(name)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)
            log_dir = Path(__file__).resolve().parent.parent / "data" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "rag.log", encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(file_handler)
        self.logger.setLevel(logging.INFO)

    def event(self, event: str, **data: Any) -> None:
        payload = {
            "event": event,
            "timestamp": time.time(),
            **data,
        }
        self.logger.info(json.dumps(payload, ensure_ascii=False, default=str))

    def duration(self, event: str, started: float, **data: Any) -> None:
        self.event(event, elapsed_ms=round((time.time() - started) * 1000, 2), **data)
