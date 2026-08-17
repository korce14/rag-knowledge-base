from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        queue = self.hits[key]
        while queue and now - queue[0] >= self.window_seconds:
            queue.popleft()
        if len(queue) >= self.limit:
            return False
        queue.append(now)
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.limiter = SlidingWindowLimiter(settings.api_rate_limit_per_minute, 60)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            auth = request.headers.get("authorization", "")
            token = auth.split(" ")[-1] if auth else ""
            client = (
                hashlib.sha256(token.encode("utf-8")).hexdigest()
                if token
                else (request.client.host if request.client else "unknown")
            )
            if not self.limiter.allow(client):
                return JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429)
        return await call_next(request)
