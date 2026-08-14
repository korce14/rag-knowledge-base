"""Migrate legacy NumPy vectors to Qdrant.

Run from the project root:

    python scripts/migrate_vectors_to_qdrant.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.config import settings
from app.vector_store import QdrantVectorStore


def main() -> None:
    if not settings.qdrant_enabled:
        raise SystemExit("请先配置 RAG_QDRANT_URL，再运行迁移脚本。")

    vector_dir = settings.data_dir / "vectors"
    if not vector_dir.exists():
        raise SystemExit(f"没有找到旧向量目录：{vector_dir}")

    store = QdrantVectorStore()
    migrated = 0
    for path in vector_dir.glob("*.npz"):
        kb_id = path.stem
        data = np.load(path, allow_pickle=True)
        ids = [str(item) for item in data["ids"].tolist()]
        doc_ids = [str(item) for item in data["doc_ids"].tolist()]
        vectors = np.asarray(data["vectors"], dtype=np.float32)
        if not ids or vectors.size == 0:
            continue
        store.upsert(kb_id, ids, doc_ids, vectors)
        migrated += len(ids)
        print(f"migrated {kb_id}: {len(ids)} vectors")

    print(f"done: {migrated} vectors")


if __name__ == "__main__":
    main()
