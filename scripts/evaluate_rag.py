"""RAG evaluation script.

Input JSONL format:
{"question": "...", "expected_document_ids": ["doc_1", "doc_2"]}

Usage:
python scripts/evaluate_rag.py --kb-id <kb_id> --input eval.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx


def load_queries(path: Path) -> list[dict]:
    queries = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    return queries


def login(client: httpx.Client, base_url: str) -> dict[str, str]:
    username = os.getenv("RAG_USERNAME", "korce")
    password = os.getenv("RAG_PASSWORD", "change-me")
    response = client.post(f"{base_url}/api/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def evaluate(base_url: str, kb_id: str, queries: list[dict], top_k: int = 5) -> dict:
    recall_sum = 0.0
    reciprocal_rank_sum = 0.0
    with httpx.Client(timeout=30.0) as client:
        headers = login(client, base_url)
        for query in queries:
            expected = set(query.get("expected_document_ids", []))
            response = client.post(
                f"{base_url}/api/search",
                headers=headers,
                json={"kb_id": kb_id, "query": query["question"], "top_k": top_k},
            )
            response.raise_for_status()
            hits = response.json().get("results", [])
            hit_documents = [hit.get("document_id") or hit.get("id") for hit in hits]
            if expected:
                recall_sum += len(expected.intersection(hit_documents)) / len(expected)
            for rank, document_id in enumerate(hit_documents, start=1):
                if document_id in expected:
                    reciprocal_rank_sum += 1 / rank
                    break

    count = max(len(queries), 1)
    return {
        "queries": len(queries),
        "recall_at_k": round(recall_sum / count, 4),
        "mrr": round(reciprocal_rank_sum / count, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("RAG_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    queries = load_queries(args.input)
    result = evaluate(args.base_url, args.kb_id, queries, args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

