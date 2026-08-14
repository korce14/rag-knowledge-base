from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import app

SAMPLE_DOCS = {
    "nutrition.txt": "苹果含有维生素 C、膳食纤维和多种抗氧化物质，是一种常见水果。",
    "quantum.txt": "量子计算利用量子比特叠加和纠缠，与经典比特计算有本质区别。",
    "hr_policy.txt": "公司考勤制度要求员工工作日 9 点前到岗，请假需要提前一天审批。",
}

SAMPLE_QUERIES = [
    {"question": "苹果有什么营养", "expected_keywords": ["维生素", "膳食纤维"]},
    {"question": "量子计算与经典计算的区别", "expected_keywords": ["量子比特", "纠缠"]},
    {"question": "公司考勤制度是什么", "expected_keywords": ["考勤", "请假"]},
]


def main() -> None:
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "korce", "password": os.getenv("RAG_PASSWORD", "change-me")})
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    kb = client.post("/api/knowledge_bases", json={"name": "HitRate 临时评测库"}, headers=headers)
    kb.raise_for_status()
    kb_id = kb.json()["id"]

    temp_files = []
    try:
        for filename, content in SAMPLE_DOCS.items():
            path = Path(tempfile.gettempdir()) / filename
            path.write_text(content, encoding="utf-8")
            temp_files.append(path)
            with path.open("rb") as file:
                upload = client.post(
                    f"/api/knowledge_bases/{kb_id}/documents",
                    headers=headers,
                    files={"file": (filename, file, "text/plain")},
                    data={"tags": ""},
                )
                if upload.status_code != 200:
                    print(f"upload failed {filename}: {upload.text}")
                    return

        hits = 0
        for query in SAMPLE_QUERIES:
            response = client.post(
                "/api/search",
                headers=headers,
                json={"kb_id": kb_id, "query": query["question"], "top_k": 3},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            text = "\n".join(item.get("text", "") for item in results)
            matched = any(keyword in text for keyword in query["expected_keywords"])
            hits += int(matched)
            print(f"query={query['question']} hit={matched}")

        hit_rate = hits / len(SAMPLE_QUERIES)
        print(json.dumps({"queries": len(SAMPLE_QUERIES), "hits": hits, "hit_rate": round(hit_rate, 4)}))
    finally:
        for path in temp_files:
            path.unlink(missing_ok=True)
        docs = client.get(f"/api/knowledge_bases/{kb_id}/documents", headers=headers).json()
        for doc in docs:
            client.delete(f"/api/documents/{doc['id']}", headers=headers)
        client.delete(f"/api/knowledge_bases/{kb_id}", headers=headers)


if __name__ == "__main__":
    main()

