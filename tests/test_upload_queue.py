from __future__ import annotations

import time
from pathlib import Path

from app.config import settings
from app.models import Role, User
from app.pipeline import KnowledgeBaseService


def _admin() -> User:
    return User(id="user_test", username="test", password_hash="x", role=Role.ADMIN)


def test_upload_task_queue_processes_and_reports_result(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "")

    service = KnowledgeBaseService()
    source = tmp_path / "sample.txt"
    source.write_text("hello", encoding="utf-8")

    def fake_ingest(actor, kb_id, file_path, tags=None, access_mode="public"):
        assert Path(file_path).name == "sample.txt"
        return {"document": {"id": "doc_1", "name": "sample.txt"}, "chunk_count": 1}

    monkeypatch.setattr(service, "ingest_path_with_mode", fake_ingest)
    submitted = service.submit_ingestion(_admin(), "kb_1", source, ["tag"])

    deadline = time.time() + 5
    task = service.get_upload_task(submitted["task_id"])
    while task is not None and task["status"] not in {"done", "error"} and time.time() < deadline:
        time.sleep(0.05)
        task = service.get_upload_task(submitted["task_id"])

    assert task is not None
    assert task["status"] == "done"
    assert task["result"]["document"]["name"] == "sample.txt"


def test_upload_task_queue_reports_failure_and_cleans_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "")

    service = KnowledgeBaseService()
    source = tmp_path / "bad.txt"
    source.write_text("broken", encoding="utf-8")

    def fake_ingest(actor, kb_id, file_path, tags=None, access_mode="public"):
        raise RuntimeError("parse error")

    monkeypatch.setattr(service, "ingest_path_with_mode", fake_ingest)
    submitted = service.submit_ingestion(_admin(), "kb_1", source)

    deadline = time.time() + 5
    task = service.get_upload_task(submitted["task_id"])
    while task is not None and task["status"] not in {"done", "error"} and time.time() < deadline:
        time.sleep(0.05)
        task = service.get_upload_task(submitted["task_id"])

    assert task is not None
    assert task["status"] == "error"
    assert not source.exists()
