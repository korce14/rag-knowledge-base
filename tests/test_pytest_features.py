from __future__ import annotations

import tempfile
from pathlib import Path

from app.models import DocumentRecord, Role
from app.pipeline_scheduler import AgentStep, CacheStep, GuardStep, PipelineContext, PipelineScheduler, RetrieveStep
from app.sql_safe import safe_eval_filtered
from app.storage import Database
from app.components import AccessControlComponent


class FakeGuardResult:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason


def test_safe_eval_filtered_truncates_string():
    assert safe_eval_filtered("'a' * 100", max_str_length=5) == "aaaaa"


def test_document_share_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        kb = db.create_kb("共享库")
        doc = DocumentRecord(
            id="doc_1",
            kb_id=kb.id,
            name="shared.txt",
            file_path="shared.txt",
            content_hash="hash",
            access_mode="restricted",
        )
        db.add_document(doc)
        owner = db.create_user("owner", "hash", Role.EDITOR)
        viewer = db.create_user("viewer", "hash", Role.VIEWER)

        db.share_document(doc.id, viewer.id, owner.id)
        assert doc.id in db.list_shared_document_ids(viewer.id)

        db.revoke_document_share(doc.id, viewer.id)
        assert doc.id not in db.list_shared_document_ids(viewer.id)


def test_pipeline_scheduler_stops_on_guard_block():
    steps = [
        GuardStep(lambda value: FakeGuardResult(False, "blocked")),
        CacheStep(lambda key: None),
        RetrieveStep(lambda *args, **kwargs: [], lambda hits: []),
        AgentStep(
            lambda *args, **kwargs: ("answer", 1, "ok"),
            lambda *args, **kwargs: "fallback",
            lambda query, answer: FakeGuardResult(True),
            lambda *args, **kwargs: None,
            lambda *args, **kwargs: None,
        ),
    ]
    context = PipelineContext(actor=None, kb_id="kb", question="test")
    context.state["cache_key"] = "qa:test"
    result = PipelineScheduler(steps).run(context)
    assert result["route"] == "blocked"


def test_access_control_shared_document_is_visible():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        kb = db.create_kb("共享库")
        owner = db.create_user("owner", "hash", Role.EDITOR)
        viewer = db.create_user("viewer", "hash", Role.VIEWER)
        doc = DocumentRecord(id="doc_1", kb_id=kb.id, name="s.txt", file_path="s.txt", content_hash="hash", access_mode="restricted")
        db.add_document(doc)
        db.share_document(doc.id, viewer.id, owner.id)
        access = AccessControlComponent(db)
        assert doc.id in access.visible_document_ids(viewer, kb.id)
