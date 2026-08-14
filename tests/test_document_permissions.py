from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models import DocumentRecord, Role, User
from app.storage import Database


class DocumentPermissionTests(unittest.TestCase):
    def test_restricted_document_permission_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            kb = db.create_kb("权限库")
            doc = DocumentRecord(id="doc_1", kb_id=kb.id, name="secret.txt", file_path="secret.txt", content_hash="hash", access_mode="restricted")
            db.add_document(doc)
            user = db.create_user("alice", "hash", Role.VIEWER)
            self.assertIsNone(db.get_user_document_permission(user.id, doc.id))
            db.grant_document_permission(user.id, doc.id, Role.VIEWER)
            permission = db.get_user_document_permission(user.id, doc.id)
            self.assertIsNotNone(permission)
            assert permission is not None
            self.assertEqual(permission.role, Role.VIEWER)
            db.revoke_document_permission(user.id, doc.id)
            self.assertIsNone(db.get_user_document_permission(user.id, doc.id))


if __name__ == "__main__":
    unittest.main()
