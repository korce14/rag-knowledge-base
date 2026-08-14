from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models import Chunk, DocumentRecord, KnowledgeBase, Role, User
from app.security import hash_password, verify_password
from app.storage import Database
from app.text import BM25, chunk_text, tokenize
from app.vector_store import QdrantVectorStore


class FakeEmbedder:
    available = False

    def embed_query(self, text: str):
        raise RuntimeError("should not be called")


class FakeVectorStore:
    def search(self, kb_id: str, query_vector, top_k: int = 10, allowed_ids: set[str] | None = None):
        return []


class TextTests(unittest.TestCase):
    def test_tokenize_handles_chinese_and_english(self):
        tokens = tokenize("深度学习 RAG 系统")
        self.assertTrue(any("深度" in token for token in tokens))
        self.assertIn("RAG", tokens)

    def test_chunk_text_respects_overlap(self):
        text = "第一段内容。" * 300
        chunks = chunk_text(text, chunk_size=500, overlap=80)
        self.assertTrue(len(chunks) > 1)
        self.assertTrue(all(len(chunk) <= 520 for chunk in chunks))


class StorageTests(unittest.TestCase):
    def test_document_and_chunk_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            kb = db.create_kb("测试库")
            doc_id = "doc_1"
            db.add_document(
                DocumentRecord(
                    id=doc_id,
                    kb_id=kb.id,
                    name="a.txt",
                    file_path="a.txt",
                    content_hash="abc",
                    status="ready",
                    created_at="2026-08-13T00:00:00+00:00",
                )
            )
            chunks = [
                Chunk(id="chunk_1", kb_id=kb.id, document_id=doc_id, document_name="a.txt", text="你好", index=0),
                Chunk(id="chunk_2", kb_id=kb.id, document_id=doc_id, document_name="a.txt", text="世界", index=1),
            ]
            db.replace_chunks(doc_id, chunks)
            self.assertEqual(len(db.list_chunks(kb.id)), 2)
            db.delete_document(doc_id)
            self.assertEqual(db.list_documents(kb.id), [])

    def test_user_and_permission_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            kb = db.create_kb("权限库")
            user = db.create_user("alice", hash_password("password123"), Role.VIEWER)
            db.grant_kb_permission(user.id, kb.id, Role.EDITOR)
            permission = db.get_user_kb_permission(user.id, kb.id)
            self.assertIsNotNone(permission)
            assert permission is not None
            self.assertEqual(permission.role, Role.EDITOR)
            db.revoke_kb_permission(user.id, kb.id)
            self.assertIsNone(db.get_user_kb_permission(user.id, kb.id))

    def test_password_hash_verification(self):
        encoded = hash_password("password123")
        self.assertTrue(verify_password("password123", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))


class VectorStoreTests(unittest.TestCase):
    def test_disabled_qdrant_store_is_noop(self):
        store = QdrantVectorStore()
        if store.enabled:
            self.skipTest("Qdrant 已配置，当前环境不执行无配置测试")
        store.upsert("kb", ["c1"], ["doc1"], __import__("numpy").array([[1, 0]], dtype="float32"))
        self.assertEqual(store.count("kb"), 0)
        self.assertEqual(store.search("kb", __import__("numpy").array([1, 0], dtype="float32")), [])


class RetrieverTests(unittest.TestCase):
    def test_lexical_retrieval_ranks_matching_chunk(self):
        from app.retriever import Retriever

        chunks = [
            Chunk(id="a", kb_id="kb", document_id="d1", document_name="a.txt", text="苹果是一种常见水果", index=0),
            Chunk(id="b", kb_id="kb", document_id="d2", document_name="b.txt", text="量子计算利用量子比特", index=0),
        ]
        retriever = Retriever(chunks, "kb", FakeVectorStore(), FakeEmbedder())
        hits = retriever.search("苹果有哪些特点", top_k=1)
        self.assertEqual(hits[0].chunk.id, "a")


if __name__ == "__main__":
    unittest.main()
