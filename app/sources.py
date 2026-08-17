from __future__ import annotations

import json
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

from .config import settings


def _write_and_ingest(service: Any, actor: Any, kb_id: str, name: str, content: str) -> None:
    if not content or not content.strip():
        return
    temp_dir = settings.data_dir / "sources" / kb_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(char for char in name if char not in '<>:"/\\|?*').strip(" .") or "source.txt"
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}.txt"
    temp = temp_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    temp.write_text(content, encoding="utf-8")
    try:
        service.ingest_path_with_mode(actor, kb_id, temp, ["source"], "public")
    finally:
        temp.unlink(missing_ok=True)


def sync_rss(service: Any, actor: Any, kb_id: str, config: dict[str, Any]) -> dict[str, Any]:
    url = str(config.get("url", "")).strip()
    if not url:
        raise ValueError("RSS 数据源缺少 url")
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    items: list[tuple[str, str]] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        content = f"标题：{title}\n\n{description}\n\n来源链接：{link}"
        items.append((title or f"rss_{len(items) + 1}", content))
    for name, content in items:
        _write_and_ingest(service, actor, kb_id, name, content)
    return {"synced": len(items), "kind": "rss"}


def sync_db(service: Any, actor: Any, kb_id: str, config: dict[str, Any]) -> dict[str, Any]:
    db_path = str(config.get("db_path", "")).strip()
    query = str(config.get("query", "")).strip()
    if not db_path or not query:
        raise ValueError("数据库数据源缺少 db_path 或 query")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()
    for index, row in enumerate(rows):
        title = str(row.get("title") or row.get("name") or f"db_row_{index + 1}")
        content = json.dumps(row, ensure_ascii=False)
        _write_and_ingest(service, actor, kb_id, title, content)
    return {"synced": len(rows), "kind": "db"}


def sync_api(service: Any, actor: Any, kb_id: str, config: dict[str, Any]) -> dict[str, Any]:
    url = str(config.get("url", "")).strip()
    if not url:
        raise ValueError("API 数据源缺少 url")
    headers = {str(key): str(value) for key, value in (config.get("headers") or {}).items()}
    response = httpx.get(url, headers=headers or None, timeout=30, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        data = data.get("items") or data.get("data") or data.get("results") or []
    if not isinstance(data, list):
        data = [data]
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            item = {"content": str(item)}
        title = str(item.get("title") or item.get("name") or f"api_row_{index + 1}")
        content = json.dumps(item, ensure_ascii=False)
        _write_and_ingest(service, actor, kb_id, title, content)
    return {"synced": len(data), "kind": "api"}


def sync_source(service: Any, actor: Any, source: dict[str, Any]) -> dict[str, Any]:
    kind = source.get("kind", "")
    config = source.get("config") or {}
    kb_id = source.get("kb_id", "")
    if kind == "rss":
        return sync_rss(service, actor, kb_id, config)
    if kind == "db":
        return sync_db(service, actor, kb_id, config)
    if kind == "api":
        return sync_api(service, actor, kb_id, config)
    raise ValueError(f"不支持的数据源类型：{kind}")
