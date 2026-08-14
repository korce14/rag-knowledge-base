from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.feedback import FeedbackWeights, record_feedback
from app.models import DocumentRecord, ScoredChunk, Chunk
from app.storage import Database


class FeedbackWeightTests(unittest.TestCase):
    def test_positive_feedback_increases_document_weight(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            kb = db.create_kb("测试库")
            doc = DocumentRecord(id="doc_1", kb_id=kb.id, name="a.txt", file_path="a.txt", content_hash="hash", access_mode="public")
            db.add_document(doc)
            chunk = Chunk(id="c1", kb_id=kb.id, document_id=doc.id, document_name="a.txt", text="内容", index=0)
            db.replace_chunks(doc.id, [chunk])

            record_feedback(db, "s1", "问题", "答案", "good", kb_id=kb.id, document_ids=[doc.id])
            weights = FeedbackWeights(db)
            weighted = weights.apply(kb.id, [ScoredChunk(chunk=chunk, score=1.0)])
            self.assertGreater(weighted[0].score, 1.0)


if __name__ == "__main__":
    unittest.main()
