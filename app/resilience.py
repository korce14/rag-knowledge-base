from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import settings


class JsonCache:
    def __init__(self) -> None:
        self._redis: Any | None = None
        self._memory: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return settings.redis_enabled

    def _get_redis(self) -> Any:
        if not self.enabled:
            return None
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._redis

    def get(self, key: str) -> str | None:
        client = self._get_redis()
        if client is not None:
            try:
                return client.get(key)
            except Exception:
                return None

        with self._lock:
            item = self._memory.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < time.time():
                self._memory.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds or settings.cache_ttl_seconds
        client = self._get_redis()
        if client is not None:
            try:
                client.set(key, value, ex=ttl)
                return
            except Exception:
                pass

        with self._lock:
            self._memory[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        client = self._get_redis()
        if client is not None:
            try:
                client.delete(key)
                return
            except Exception:
                pass
        with self._lock:
            self._memory.pop(key, None)

    def clear(self) -> None:
        client = self._get_redis()
        if client is not None:
            try:
                keys = client.keys("qa:*")
                if keys:
                    client.delete(*keys)
            except Exception:
                pass
        with self._lock:
            self._memory.clear()


class BloomFilter:
    """Redis SETBIT 实现的 BloomFilter，无 Redis 时退回内存位数组。"""

    def __init__(self, name: str, capacity: int | None = None, error_rate: float | None = None):
        self.name = name
        self.capacity = capacity or settings.bloom_capacity
        self.error_rate = error_rate or settings.bloom_error_rate
        self.num_bits = self._bit_size(self.capacity, self.error_rate)
        self.num_hashes = self._hash_count(self.num_bits, self.capacity)
        self._redis: Any | None = None
        self._local = bytearray((self.num_bits + 7) // 8)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return settings.redis_enabled

    @staticmethod
    def _bit_size(n: int, p: float) -> int:
        import math

        size = int(-(n * math.log(p)) / (math.log(2) ** 2))
        return max(size, 1024)

    @staticmethod
    def _hash_count(m: int, n: int) -> int:
        count = int((m / n) * 0.6931471805599453)
        return max(count, 3)

    def _positions(self, value: str) -> list[int]:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        positions: list[int] = []
        for index in range(self.num_hashes):
            seed = digest[(index * 2) % len(digest)] + (index << 8)
            offset = (seed * 0x9E3779B1) % self.num_bits
            positions.append(int(offset))
        return positions

    def _get_redis(self) -> Any:
        if not self.enabled:
            return None
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=False,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._redis

    def add(self, value: str) -> None:
        positions = self._positions(value)
        client = self._get_redis()
        if client is not None:
            key = f"bloom:{self.name}"
            try:
                pipe = client.pipeline()
                for position in positions:
                    pipe.setbit(key, position, 1)
                pipe.execute()
                return
            except Exception:
                pass

        with self._lock:
            for position in positions:
                byte_index = position // 8
                bit_index = position % 8
                self._local[byte_index] |= 1 << bit_index

    def contains(self, value: str) -> bool:
        positions = self._positions(value)
        client = self._get_redis()
        if client is not None:
            key = f"bloom:{self.name}"
            try:
                pipe = client.pipeline()
                for position in positions:
                    pipe.getbit(key, position)
                bits = pipe.execute()
                return all(bool(bit) for bit in bits)
            except Exception:
                pass

        with self._lock:
            return all(bool(self._local[position // 8] & (1 << (position % 8))) for position in positions)


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float = 0.0
    state: str = "closed"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int | None = None,
        recovery_timeout: int | None = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold or settings.circuit_failure_threshold
        self.recovery_timeout = recovery_timeout or settings.circuit_recovery_timeout_seconds
        self._state = _CircuitState()
        self._lock = threading.Lock()

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        self._before_call()
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    async def call_async(self, func: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        self._before_call()
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _before_call(self) -> None:
        with self._lock:
            if self._state.state == "open":
                if time.time() - self._state.opened_at >= self.recovery_timeout:
                    self._state.state = "half_open"
                else:
                    raise CircuitOpenError(f"服务 {self.name} 已熔断")

    def _on_success(self) -> None:
        with self._lock:
            self._state.failures = 0
            self._state.state = "closed"
            self._state.opened_at = 0.0

    def _on_failure(self) -> None:
        with self._lock:
            self._state.failures += 1
            if self._state.failures >= self.failure_threshold:
                self._state.state = "open"
                self._state.opened_at = time.time()

    @property
    def state(self) -> str:
        return self._state.state


class Resilience:
    def __init__(self) -> None:
        self.cache = JsonCache()
        self.query_cache = BloomFilter("query_cache")
        self.document_seen = BloomFilter("documents")
        self.blocked_prompt = BloomFilter("blocked_prompts")
        self.generation_breaker = CircuitBreaker("generation")
        self.embedding_breaker = CircuitBreaker("embedding")
        self.rerank_breaker = CircuitBreaker("rerank")
