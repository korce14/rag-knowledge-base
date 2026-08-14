from __future__ import annotations

import unittest

from app.resilience import BloomFilter, CircuitBreaker, CircuitOpenError, JsonCache


class ResilienceTests(unittest.TestCase):
    def test_memory_cache_fallback(self):
        cache = JsonCache()
        cache.set("key", "value", ttl_seconds=10)
        self.assertEqual(cache.get("key"), "value")
        cache.delete("key")
        self.assertIsNone(cache.get("key"))

    def test_bloom_filter_local_round_trip(self):
        bloom = BloomFilter("test")
        bloom.add("doc_1")
        self.assertTrue(bloom.contains("doc_1"))
        self.assertFalse(bloom.contains("doc_2"))

    def test_circuit_breaker_opens_after_failures(self):
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=30)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        with self.assertRaises(CircuitOpenError):
            breaker.call(lambda: "should not run")


if __name__ == "__main__":
    unittest.main()
