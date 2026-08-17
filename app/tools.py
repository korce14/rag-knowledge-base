from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from .config import settings
from .sql_safe import safe_eval, validate_sql


def calculate(expression: str) -> float:
    return float(safe_eval(expression))


def sql_query(query: str) -> list[dict[str, Any]]:
    if not validate_sql(query):
        raise ValueError("只允许只读 SELECT 查询")
    conn = sqlite3.connect(settings.data_dir / "knowledge.db")
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()


def plot_chart(kind: str, x: list[float], y: list[float], title: str = "chart") -> str:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError("matplotlib 未安装")

    figure, axis = plt.subplots(figsize=(6, 4))
    if kind == "bar":
        axis.bar(x, y)
    elif kind == "line":
        axis.plot(x, y)
    elif kind == "pie":
        axis.pie(y, labels=[str(item) for item in x])
    else:
        axis.scatter(x, y)
    axis.set_title(title)
    output_dir = settings.data_dir / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{title}.png"
    figure.savefig(path)
    plt.close(figure)
    return str(path)


def retrieve(kb_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    from .pipeline import KnowledgeBaseService

    service = KnowledgeBaseService()
    admin = service.db.get_user_by_username(settings.admin_username) or service.db.get_user_by_username("korce")
    if admin is None:
        return []
    hits = service.search_visible(admin, kb_id, query, top_k=top_k)
    return [
        {
            "document_name": hit.chunk.document_name,
            "text": hit.chunk.text,
            "score": round(hit.score, 4),
        }
        for hit in hits
    ]
