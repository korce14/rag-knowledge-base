from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import httpx

BASE_URL = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8000")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--username", default="korce")
    parser.add_argument("--password", default=os.getenv("RAG_PASSWORD", "change-me"))
    args = parser.parse_args()

    login = httpx.post(f"{BASE_URL}/api/auth/login", json={"username": args.username, "password": args.password})
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    with args.file.open("r", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            filename = row.get("filename", "batch.txt")
            content = row.get("content", "")
            tags = row.get("tags", "")
            with httpx.Client() as client:
                response = client.post(
                    f"{BASE_URL}/api/knowledge_bases/{args.kb_id}/documents",
                    headers=headers,
                    files={"file": (filename, content.encode("utf-8"), "text/plain")},
                    data={"tags": tags},
                )
                print(filename, response.status_code)
                if response.status_code != 200:
                    print(response.text)


if __name__ == "__main__":
    main()
