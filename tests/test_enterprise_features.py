from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from app.batch_import import import_tabular, index_folder
from app.config import settings
from app.models import DocumentRecord, Role, User
from app.pipeline import KnowledgeBaseService
from app.security import hash_api_key, hash_password, verify_password
from app.storage import Database


def _make_service(monkeypatch, tmp_path) -> KnowledgeBaseService:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "qdrant_url", "")
    monkeypatch.setattr(settings, "generation_api_key", "")
    return KnowledgeBaseService()


def _admin() -> User:
    return User(id="admin", username="admin", password_hash="x", role=Role.ADMIN)


def test_storage_api_key_round_trip(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    user = db.create_user("alice", "hash", Role.EDITOR)
    record = db.create_api_key("ci", user.id, "digest")
    assert db.get_api_key_by_hash("digest") is not None
    assert db.list_api_keys(user.id)[0]["name"] == "ci"
    db.revoke_api_key(record["id"], user.id)
    assert db.get_api_key_by_hash("digest") is None


def test_storage_source_round_trip(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    kb = db.create_kb("src")
    created = db.create_source(kb.id, "rss", "feed", {"url": "https://x"})
    listed = db.list_sources(kb.id)
    assert listed[0]["id"] == created["id"]
    db.update_source(created["id"], enabled=False, interval_minutes=5)
    updated = db.list_sources(kb.id)[0]
    assert updated["enabled"] == 0
    assert updated["interval_minutes"] == 5
    db.delete_source(created["id"])
    assert db.list_sources(kb.id) == []


def test_storage_bm25_round_trip(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.save_bm25_tokens("kb", {"c1": ("d1", "苹果好吃", ["苹果", "好吃"]), "c2": ("d2", "量子计算", ["量子", "计算"])})
    loaded = db.get_bm25_tokens("kb")
    assert loaded["c1"]["tokens"] == ["苹果", "好吃"]
    db.delete_bm25_tokens(document_id="d1")
    assert "c1" not in db.get_bm25_tokens("kb")


def test_storage_kb_toc_round_trip(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    kb = db.create_kb("toc")
    db.set_kb_toc(kb.id, [{"id": "1", "title": "实验"}], "概述")
    toc, overview = db.get_kb_toc(kb.id)
    assert toc[0]["title"] == "实验"
    assert overview == "概述"


def test_service_change_password(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    user = service.db.create_user("alice", hash_password("oldpass123"), Role.VIEWER)
    actor = service.db.get_user(user.id)
    assert actor is not None
    service.change_password(actor, "oldpass123", "newpass456")
    updated = service.db.get_user(user.id)
    assert updated is not None
    assert verify_password("newpass456", updated.password_hash)


def test_service_reset_password(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    user = service.db.create_user("bob", hash_password("oldpass123"), Role.VIEWER)
    service.reset_password(_admin(), user.id, "resetpass789")
    updated = service.db.get_user(user.id)
    assert updated is not None
    assert verify_password("resetpass789", updated.password_hash)


def test_service_api_key_lifecycle(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    admin = service.db.create_user("root", "x", Role.ADMIN)
    result = service.create_api_key(admin, "ci")
    assert result["key"].startswith("rag_")
    assert service.db.get_api_key_by_hash(hash_api_key(result["key"])) is not None
    service.revoke_api_key(admin, result["id"])
    assert service.db.get_api_key_by_hash(hash_api_key(result["key"])) is None


def test_service_source_lifecycle(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("src")
    created = service.create_source(_admin(), kb.id, "api", "api-source", {"url": "https://x"})
    service.update_source(_admin(), created["id"], enabled=False)
    assert service.list_sources(_admin(), kb.id)[0]["enabled"] == 0
    service.delete_source(_admin(), created["id"])
    assert service.list_sources(_admin(), kb.id) == []


def test_batch_import_csv(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("batch")
    source = tmp_path / "batch.csv"
    with source.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["filename", "content"])
        writer.writeheader()
        writer.writerow({"filename": "a.txt", "content": "苹果好吃"})
        writer.writerow({"filename": "b.txt", "content": "量子计算"})
    result = import_tabular(service, _admin(), kb.id, source, ["tag"])
    assert result["imported"] == 2
    assert len(service.db.list_documents(kb.id)) == 2


def test_folder_index(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("folder")
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "one.txt").write_text("第一份文档", encoding="utf-8")
    (folder / "two.md").write_text("第二份文档", encoding="utf-8")
    (folder / "skip.bin").write_bytes(b"x")
    result = index_folder(service, _admin(), kb.id, folder)
    assert result["imported"] == 2
    assert len(service.db.list_documents(kb.id)) == 2


class _FakeResponse:
    def __init__(self, text: str = "", data: object = None):
        self.text = text
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._data


def test_sync_rss_source(monkeypatch, tmp_path) -> None:
    import app.sources as sources_mod

    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("rss")
    rss = """<rss><channel><item><title>标题一</title><description>内容一</description><link>https://x/1</link></item></channel></rss>"""
    monkeypatch.setattr(sources_mod.httpx, "get", lambda url, **kwargs: _FakeResponse(text=rss))
    result = sources_mod.sync_rss(service, _admin(), kb.id, {"url": "https://feed"})
    assert result["synced"] == 1
    assert len(service.db.list_documents(kb.id)) == 1


def test_sync_api_source(monkeypatch, tmp_path) -> None:
    import app.sources as sources_mod

    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("api")
    monkeypatch.setattr(
        sources_mod.httpx,
        "get",
        lambda url, **kwargs: _FakeResponse(data={"items": [{"title": "记录一", "value": 1}, {"title": "记录二", "value": 2}]}),
    )
    result = sources_mod.sync_api(service, _admin(), kb.id, {"url": "https://api"})
    assert result["synced"] == 2
    assert len(service.db.list_documents(kb.id)) == 2


def test_sync_db_source(monkeypatch, tmp_path) -> None:
    import app.sources as sources_mod

    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("db")
    db_path = tmp_path / "external.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE items (title TEXT, value INTEGER)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", [("甲", 1), ("乙", 2)])
    conn.commit()
    conn.close()
    result = sources_mod.sync_db(service, _admin(), kb.id, {"db_path": str(db_path), "query": "SELECT * FROM items"})
    assert result["synced"] == 2
    assert len(service.db.list_documents(kb.id)) == 2


def test_generate_kb_toc_fallback(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("toc")
    service.db.add_document(
        DocumentRecord(id="doc1", kb_id=kb.id, name="实验一.docx", file_path="x", content_hash="h1")
    )
    result = service.generate_kb_toc(_admin(), kb.id)
    assert result["toc"][0]["title"] == "实验一.docx"
    assert "1 个文档" in result["overview"]


def test_analytics_questions_and_report(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("analytics")
    service.db.add_retrieval_gap(kb.id, "u1", "什么是 802.15.4", 0.2)
    service.db.add_retrieval_gap(kb.id, "u1", "什么是 802.15.4", 0.4)
    service.db.add_retrieval_gap(kb.id, "u1", "RSSI 是什么", 0.1)
    questions = service.analytics_questions(_admin(), kb.id)
    assert questions[0]["question"] == "什么是 802.15.4"
    assert questions[0]["count"] == 2
    report = service.analytics_report(_admin(), kb.id)
    assert report["total_questions"] == 3
    assert "问题,次数" in service.export_analytics_report(_admin(), kb.id)


def test_analytics_cards_include_summary(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("cards")
    service.db.add_retrieval_gap(kb.id, "u1", "什么是 802.15.4", 0.2)
    service.db.add_retrieval_gap(kb.id, "u1", "什么是 802.15.4", 0.4)
    cards = service.analytics_cards(_admin(), kb.id)
    assert len(cards) == 1
    assert cards[0]["count"] == 2
    assert cards[0]["summary"]
    html = service.export_analytics_card(_admin(), kb.id, cards[0]["id"])
    assert "问题分组分析" in html


def test_generate_kb_overview_fallback(monkeypatch, tmp_path) -> None:
    service = _make_service(monkeypatch, tmp_path)
    kb = service.db.create_kb("overview")
    service.db.add_document(DocumentRecord(id="doc1", kb_id=kb.id, name="报告.docx", file_path="x", content_hash="h"))
    result = service.generate_kb_overview(_admin(), kb.id)
    assert "1 个文档" in result["overview"]
