"""Lightweight RAG load test without external load-testing tools.

Usage:
python scripts/load_test.py --kb-id <kb_id> --concurrency 10 --requests 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import httpx


async def worker(client: httpx.AsyncClient, headers: dict, base_url: str, kb_id: str, question: str) -> tuple[bool, float]:
    started = time.perf_counter()
    try:
        response = await client.post(
            f"{base_url}/api/search",
            headers=headers,
            json={"kb_id": kb_id, "query": question, "top_k": 5},
        )
        return response.status_code == 200, time.perf_counter() - started
    except Exception:
        return False, time.perf_counter() - started


async def run(base_url: str, kb_id: str, concurrency: int, total: int) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        login = await client.post(
            f"{base_url}/api/auth/login",
            json={
                "username": os.getenv("RAG_USERNAME", "korce"),
                "password": os.getenv("RAG_PASSWORD", "change-me"),
            },
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        queue: asyncio.Queue[str] = asyncio.Queue()
        for _ in range(total):
            queue.put_nowait("RAG 知识库性能测试问题")

        latencies: list[float] = []
        failures = 0

        async def consume() -> None:
            nonlocal failures
            while not queue.empty():
                question = await queue.get()
                ok, latency = await worker(client, headers, base_url, kb_id, question)
                if ok:
                    latencies.append(latency)
                else:
                    failures += 1

        started = time.perf_counter()
        await asyncio.gather(*[consume() for _ in range(concurrency)])
        elapsed = time.perf_counter() - started

        if latencies:
            print(
                json.dumps(
                    {
                        "requests": total,
                        "concurrency": concurrency,
                        "failures": failures,
                        "elapsed_seconds": round(elapsed, 2),
                        "throughput_rps": round(total / elapsed, 2),
                        "latency_p50_ms": round(statistics.median(latencies) * 1000, 2),
                        "latency_p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)] * 1000, 2),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(json.dumps({"requests": total, "failures": failures}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("RAG_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.kb_id, args.concurrency, args.requests))


if __name__ == "__main__":
    main()

