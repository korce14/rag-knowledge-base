from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import HTTPException, status

from .config import settings
from .components import AccessControlComponent, ObservabilityComponent, RetrievalAdjustmentComponent
from .agent import RagAgent
from .feedback import FeedbackWeights
from .observability import StructuredLogger
from .documents import delete_document, ingest_file
from .documents import ingest_file_with_mode
from .embedder import Embedder
from .generator import Generator, fallback_answer
from .guard import Guard
from .models import Chunk, KnowledgeBase, QueryResult, Role, ScoredChunk, User
from .models import DocumentPermission
from .prompts import PromptManager
from .pipeline_scheduler import AgentStep, CacheStep, GuardStep, PipelineContext, PipelineScheduler, RetrieveStep
from .reranker import Reranker
from .resilience import Resilience
from .retriever import Retriever
from .security import hash_password
from .storage import Database
from .vector_store import QdrantVectorStore

_ROLE_LEVEL = {
    Role.VIEWER: 1,
    Role.EDITOR: 2,
    Role.ADMIN: 3,
}


class KnowledgeBaseService:
    def __init__(self):
        self.db = Database()
        self.prompts = PromptManager()
        self.resilience = Resilience()
        self.vector_store = QdrantVectorStore()
        self.embedder = Embedder(self.resilience.embedding_breaker)
        self.generator = Generator(self.prompts, self.resilience.generation_breaker)
        self.reranker = Reranker(self.resilience.rerank_breaker)
        self.guard = Guard(self.prompts)
        self.logger = StructuredLogger()
        self.agent = RagAgent(self.generator)
        self.feedback_weights = FeedbackWeights(self.db)
        self.access_control = AccessControlComponent(self.db)
        self.retrieval_adjustment = RetrievalAdjustmentComponent(self.feedback_weights)
        self.observability = ObservabilityComponent(self.logger)
        self.pipeline_scheduler = self._build_pipeline_scheduler()
        self._ensure_admin_user()

    # 初始化与用户

    def _ensure_admin_user(self) -> None:
        if self.db.get_user_by_username(settings.admin_username):
            return
        if not settings.admin_username or not settings.admin_password:
            return
        self.db.create_user(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role=Role.ADMIN,
        )

    def _build_pipeline_scheduler(self) -> PipelineScheduler:
        def history_loader(_: str, session_id: str):
            return self.db.list_messages(session_id)

        def message_recorder(_: str, session_id: str, question: str, answer: str):
            self.db.add_message(session_id, "user", question)
            self.db.add_message(session_id, "assistant", answer)

        def record(kind: str, session_id: str, question: str | None = None, answer: str | None = None):
            if kind == "history":
                return history_loader("history", session_id)
            message_recorder("messages", session_id, question, answer)

        steps = [
            GuardStep(self.guard.validate_input),
            CacheStep(self._cached_qa),
            RetrieveStep(self.search_visible, self._format_sources),
            AgentStep(
                self.agent.answer,
                fallback_answer,
                self.guard.validate_output,
                record,
                self._cache_qa,
            ),
        ]
        return PipelineScheduler(steps)

    def query_pipeline(
        self,
        actor: User,
        kb_id: str,
        question: str,
        session_id: str = "default",
        top_k: int = settings.top_k,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        question = self._sanitize(question)
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        context = PipelineContext(
            actor=actor,
            kb_id=kb_id,
            question=question,
            session_id=session_id,
            top_k=top_k,
            document_id=document_id,
            tags=tags,
        )
        context.state["cache_key"] = self._qa_cache_key(kb_id, question)
        result = self.pipeline_scheduler.run(context)
        self.logger.event("pipeline_done", kb_id=kb_id, route=result.get("route"), result_count=len(result.get("sources", [])))
        return result


    def login(self, username: str, password: str) -> User:
        user = self.db.get_user_by_username(username.strip())
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
        from .security import verify_password

        if not verify_password(password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
        return user
    def list_users(self, actor: User) -> list[User]:
        self._require_global_role(actor, Role.ADMIN)
        return self.db.list_users()

    def create_user(self, actor: User, username: str, password: str, role: Role, is_active: bool = True) -> User:
        self._require_global_role(actor, Role.ADMIN)
        username = username.strip()
        if not username or len(username) < 3:
            raise HTTPException(status_code=422, detail="用户名至少需要 3 个字符")
        if len(password) < 8:
            raise HTTPException(status_code=422, detail="密码至少需要 8 个字符")
        if self.db.get_user_by_username(username):
            raise HTTPException(status_code=409, detail="用户名已存在")
        return self.db.create_user(username, hash_password(password), role, is_active)

    def update_user(
        self,
        actor: User,
        user_id: str,
        *,
        role: Role | None = None,
        is_active: bool | None = None,
        password: str | None = None,
    ) -> User:
        self._require_global_role(actor, Role.ADMIN)
        if not self.db.get_user(user_id):
            raise HTTPException(status_code=404, detail="用户不存在")
        password_hash = hash_password(password) if password else None
        self.db.update_user(user_id, role=role, is_active=is_active, password_hash=password_hash)
        user = self.db.get_user(user_id)
        assert user is not None
        return user

    def delete_user(self, actor: User, user_id: str) -> None:
        self._require_global_role(actor, Role.ADMIN)
        if user_id == actor.id:
            raise HTTPException(status_code=400, detail="不能删除当前登录用户")
        if not self.db.get_user(user_id):
            raise HTTPException(status_code=404, detail="用户不存在")
        self.db.delete_user(user_id)

    # 知识库权限

    def list_kbs(self, actor: User) -> list[KnowledgeBase]:
        if actor.role == Role.ADMIN:
            return self.db.list_kbs()
        allowed_ids = set(self.db.list_user_kb_ids(actor.id))
        return [kb for kb in self.db.list_kbs() if kb.id in allowed_ids]

    def create_kb(self, actor: User, name: str, description: str = "") -> KnowledgeBase:
        if _ROLE_LEVEL[actor.role] < _ROLE_LEVEL[Role.EDITOR]:
            raise HTTPException(status_code=403, detail="当前用户没有创建知识库的权限")
        kb = self.db.create_kb(name, description)
        if actor.role != Role.ADMIN:
            self.db.grant_kb_permission(actor.id, kb.id, Role.EDITOR)
        return kb

    def delete_kb(self, actor: User, kb_id: str) -> None:
        self._require_global_role(actor, Role.ADMIN)
        if not self.db.get_kb(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        self.db.delete_kb(kb_id)
        self.vector_store.delete_kb(kb_id)
        self.resilience.cache.clear()

    def get_kb_permissions(self, actor: User, kb_id: str) -> list[dict[str, Any]]:
        self._require_global_role(actor, Role.ADMIN)
        self._ensure_kb(kb_id)
        return [
            {
                "id": item.id,
                "user_id": item.user_id,
                "kb_id": item.kb_id,
                "role": item.role.value,
                "created_at": item.created_at,
            }
            for item in self.db.get_kb_permissions(kb_id)
        ]

    def grant_kb_permission(self, actor: User, kb_id: str, user_id: str, role: Role) -> None:
        self._require_global_role(actor, Role.ADMIN)
        self._ensure_kb(kb_id)
        if not self.db.get_user(user_id):
            raise HTTPException(status_code=404, detail="用户不存在")
        self.db.grant_kb_permission(user_id, kb_id, role)

    def revoke_kb_permission(self, actor: User, kb_id: str, user_id: str) -> None:
        self._require_global_role(actor, Role.ADMIN)
        self._ensure_kb(kb_id)
        self.db.revoke_kb_permission(user_id, kb_id)

    # 文档

    def ingest_path(self, actor: User, kb_id: str, file_path: Path, tags: list[str] | None = None) -> dict[str, Any]:
        self._require_kb_access(actor, kb_id, Role.EDITOR)
        guard_result = self.guard.validate_upload(file_path.name, None, file_path.stat().st_size)
        if not guard_result.ok:
            raise HTTPException(status_code=400, detail=guard_result.reason)
        record, chunks = ingest_file(self.db, kb_id, file_path, tags)
        if self.embedder.available and chunks:
            vectors = self.embedder.embed([chunk.text for chunk in chunks])
            self.vector_store.replace_document(
                kb_id,
                record.id,
                [chunk.id for chunk in chunks],
                vectors,
            )
        self.resilience.cache.clear()
        return {
            "document": {
                "id": record.id,
                "name": record.name,
                "status": record.status,
                "created_at": record.created_at,
            },
            "chunk_count": len(chunks),
        }

    def remove_document(self, actor: User, kb_id: str, document_id: str) -> None:
        self._require_kb_access(actor, kb_id, Role.EDITOR)
        record = self.db.get_document(document_id)
        if not record or record.kb_id != kb_id:
            raise HTTPException(status_code=404, detail="文档不存在")
        delete_document(self.db, self.vector_store, kb_id, document_id)
        self.resilience.cache.clear()

    def remove_document_visible(self, actor: User, document_id: str) -> None:
        record = self.db.get_document(document_id)
        if not record:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, record, Role.EDITOR)
        delete_document(self.db, self.vector_store, record.kb_id, document_id)
        self.resilience.cache.clear()

    def ingest_path_with_mode(self, actor: User, kb_id: str, file_path: Path, tags: list[str] | None = None, access_mode: str = "public") -> dict[str, Any]:
        self._require_kb_access(actor, kb_id, Role.EDITOR)
        guard_result = self.guard.validate_upload(file_path.name, None, file_path.stat().st_size)
        if not guard_result.ok:
            raise HTTPException(status_code=400, detail=guard_result.reason)
        record, chunks = ingest_file_with_mode(self.db, kb_id, file_path, tags, access_mode)
        if access_mode == "restricted" and actor.role != Role.ADMIN:
            self.db.grant_document_permission(actor.id, record.id, Role.EDITOR)
        if self.embedder.available and chunks:
            try:
                vectors = self.embedder.embed([chunk.text for chunk in chunks])
                self.vector_store.replace_document(kb_id, record.id, [chunk.id for chunk in chunks], vectors)
            except Exception as exc:
                self.logger.event("qdrant_ingest_failed", kb_id=kb_id, document_id=record.id, error=str(exc))
        self.resilience.cache.clear()
        return {
            "document": {
                "id": record.id,
                "name": record.name,
                "access_mode": record.access_mode,
                "status": record.status,
                "created_at": record.created_at,
            },
            "chunk_count": len(chunks),
        }

    def list_documents(self, actor: User, kb_id: str) -> list[Any]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        return [record.__dict__ for record in self.db.list_documents(kb_id)]

    def list_visible_documents(self, actor: User, kb_id: str) -> list[Any]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        return [record.__dict__ for record in self._visible_documents(actor, kb_id)]
        return [record.__dict__ for record in self._visible_documents(actor, kb_id)]

    def get_document_permissions(self, actor: User, document_id: str) -> list[dict[str, Any]]:
        document = self.db.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, document, Role.EDITOR)
        return [
            {
                "id": item.id,
                "user_id": item.user_id,
                "document_id": item.document_id,
                "role": item.role.value,
                "created_at": item.created_at,
            }
            for item in self.db.get_document_permissions(document_id)
        ]

    def grant_document_permission(self, actor: User, document_id: str, user_id: str, role: Role) -> None:
        document = self.db.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, document, Role.EDITOR)
        if not self.db.get_user(user_id):
            raise HTTPException(status_code=404, detail="用户不存在")
        self.db.grant_document_permission(user_id, document_id, role)

    def revoke_document_permission(self, actor: User, document_id: str, user_id: str) -> None:
        document = self.db.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, document, Role.EDITOR)
        self.db.revoke_document_permission(user_id, document_id)

    def set_document_access_mode(self, actor: User, document_id: str, access_mode: str) -> None:
        document = self.db.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, document, Role.EDITOR)
        if access_mode not in {"public", "restricted"}:
            raise HTTPException(status_code=422, detail="access_mode 只能是 public 或 restricted")
        self.db.set_document_access_mode(document_id, access_mode)

    def share_document(self, actor: User, document_id: str, user_id: str) -> None:
        document = self.db.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, document, Role.EDITOR)
        if not self.db.get_user(user_id):
            raise HTTPException(status_code=404, detail="用户不存在")
        self.db.share_document(document_id, user_id, actor.id)

    def revoke_document_share(self, actor: User, document_id: str, user_id: str) -> None:
        document = self.db.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, document, Role.EDITOR)
        self.db.revoke_document_share(document_id, user_id)

    def list_document_shares(self, actor: User, document_id: str) -> list[dict[str, Any]]:
        document = self.db.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, document, Role.EDITOR)
        return [
            {
                "id": item.id,
                "document_id": item.document_id,
                "user_id": item.user_id,
                "created_by": item.created_by,
                "created_at": item.created_at,
            }
            for item in self.db.list_document_shares(document_id)
        ]

    def get_retriever(self, kb_id: str) -> Retriever:
        chunks = self.db.list_chunks(kb_id)
        return Retriever(chunks=chunks, kb_id=kb_id, vector_store=self.vector_store, embedder=self.embedder)

    def get_retriever_for_documents(self, kb_id: str, document_ids: list[str]) -> Retriever:
        chunks = self.db.list_chunks_by_documents(kb_id, document_ids)
        return Retriever(chunks=chunks, kb_id=kb_id, vector_store=self.vector_store, embedder=self.embedder)

    def search_visible(
        self,
        actor: User,
        kb_id: str,
        query: str,
        top_k: int = settings.top_k,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ScoredChunk]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        guard_result = self.guard.validate_input(query)
        if not guard_result.ok:
            raise HTTPException(status_code=400, detail=guard_result.reason)
        visible_ids = self._visible_document_ids(actor, kb_id)
        if document_id and document_id not in visible_ids:
            raise HTTPException(status_code=403, detail="当前用户没有该文档的访问权限")
        retriever = self.get_retriever_for_documents(kb_id, sorted(visible_ids))
        hits = retriever.search(query, top_k=max(top_k, 1), document_id=document_id, tags=tags)
        reranked = self.reranker.rerank(query, hits)
        weighted = self.feedback_weights.apply(kb_id, reranked)
        self.logger.event("retrieval_done", kb_id=kb_id, query=query[:120], result_count=len(weighted), visible_documents=len(visible_ids))
        return weighted
    def search(
        self,
        actor: User,
        kb_id: str,
        query: str,
        top_k: int = settings.top_k,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ScoredChunk]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        guard_result = self.guard.validate_input(query)
        if not guard_result.ok:
            raise HTTPException(status_code=400, detail=guard_result.reason)
        retriever = self.get_retriever(kb_id)
        hits = retriever.search(query, top_k=max(top_k, 1), document_id=document_id, tags=tags)
        return self.reranker.rerank(query, hits)

    def query(
        self,
        actor: User,
        kb_id: str,
        question: str,
        session_id: str = "default",
        top_k: int = settings.top_k,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> QueryResult:
        started = time.time()
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        guard_result = self.guard.validate_input(question)
        if not guard_result.ok:
            return QueryResult(answer=guard_result.reason, sources=[], route="blocked", elapsed_ms=0)

        question = self._sanitize(question)
        cache_key = self._qa_cache_key(kb_id, question)
        yield {"type": "progress", "stage": "cache", "message": "缓存检查中..."}
        cached = self._cached_qa(cache_key)
        if cached is not None:
            return QueryResult(
                answer=cached["answer"],
                sources=cached["sources"],
                route="cached",
                elapsed_ms=(time.time() - started) * 1000,
            )

        rewritten = self.generator.rewrite_query(question)
        hits = self.search_visible(actor, kb_id, rewritten, top_k=top_k, document_id=document_id, tags=tags)
        chunks = [hit.chunk for hit in hits]
        sources = self._format_sources(hits)

        if self.generator.available:
            history = self.db.list_messages(session_id)
            messages = self.generator._build_qa_messages(question, chunks, history)
            try:
                answer, attempts, check_reason = self.agent.answer(question, chunks, history)
                self.logger.event("generation_done", kb_id=kb_id, attempts=attempts, check_reason=check_reason)
            except Exception as exc:
                self.logger.event("generation_fallback", kb_id=kb_id, error=str(exc))
                answer = fallback_answer(question, chunks)

        output_guard = self.guard.validate_output(question, answer)
        if not output_guard.ok:
            answer = "回答被安全策略拦截，请尝试更具体或合规的问题。"

        self.db.add_message(session_id, "user", question)
        self.db.add_message(session_id, "assistant", answer)
        self._cache_qa(cache_key, answer, sources)
        return QueryResult(
            answer=answer,
            sources=sources,
            route="generated" if self.generator.available else "extractive",
            rewritten_query=rewritten,
            elapsed_ms=(time.time() - started) * 1000,
        )

    async def stream(
        self,
        actor: User,
        kb_id: str,
        question: str,
        session_id: str = "default",
        top_k: int = settings.top_k,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        yield {"type": "progress", "stage": "guard", "message": "安全校验中..."}
        guard_result = self.guard.validate_input(question)
        if not guard_result.ok:
            yield {"type": "error", "reason": guard_result.reason}
            return

        question = self._sanitize(question)
        cache_key = self._qa_cache_key(kb_id, question)
        cached = self._cached_qa(cache_key)
        if cached is not None:
            yield {"type": "sources", "sources": cached["sources"]}
            yield {"type": "token", "content": cached["answer"]}
            yield {"type": "done", "rewritten_query": question}
            return

        rewritten = self.generator.rewrite_query(question)
        yield {"type": "progress", "stage": "retrieval", "message": "检索知识库中..."}
        hits = self.search_visible(actor, kb_id, rewritten, top_k=top_k, document_id=document_id, tags=tags)
        chunks = [hit.chunk for hit in hits]
        sources = self._format_sources(hits)
        yield {"type": "sources", "sources": sources}
        yield {"type": "progress", "stage": "generation", "message": "生成回答中..."}

        answer_parts: list[str] = []
        if self.generator.available:
            history = self.db.list_messages(session_id)
            messages = self.generator._build_qa_messages(question, chunks, history)
            try:
                async for token in self.generator.stream(messages):
                    answer_parts.append(token)
                    yield {"type": "token", "content": token}
            except Exception:
                answer = fallback_answer(question, chunks)
                answer_parts = [answer]
                yield {"type": "token", "content": answer}
        else:
            answer = fallback_answer(question, chunks)
            answer_parts = [answer]
            yield {"type": "token", "content": answer}

        answer = "".join(answer_parts)
        yield {"type": "progress", "stage": "output_guard", "message": "安全审核中..."}
        output_guard = self.guard.validate_output(question, answer)
        if not output_guard.ok:
            answer = "回答被安全策略拦截，请尝试更具体或合规的问题。"
            yield {"type": "token", "content": answer}

        self.db.add_message(session_id, "user", question)
        self.db.add_message(session_id, "assistant", answer)
        self._cache_qa(cache_key, answer, sources)
        yield {"type": "done", "rewritten_query": rewritten}

    def feedback(self, session_id: str, question: str, answer: str, rating: str, kb_id: str | None = None, document_ids: list[str] | None = None) -> None:
        from .feedback import record_feedback
        record_feedback(self.db, session_id, question, answer, rating, kb_id=kb_id, document_ids=document_ids)
    # 缓存

    def _qa_cache_key(self, kb_id: str, question: str) -> str:
        digest = hashlib.sha256(f"{kb_id}:{question}".encode("utf-8")).hexdigest()
        return f"qa:{digest}"

    def _cached_qa(self, key: str) -> dict[str, Any] | None:
        if not self.resilience.query_cache.contains(key):
            return None
        raw = self.resilience.cache.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if "answer" not in data or "sources" not in data:
            return None
        return data

    def _cache_qa(self, key: str, answer: str, sources: list[dict[str, Any]]) -> None:
        self.resilience.cache.set(key, json.dumps({"answer": answer, "sources": sources}, ensure_ascii=False))
        self.resilience.query_cache.add(key)

    # 状态与能力

    def capabilities(self) -> dict[str, Any]:
        return {
            "dense_enabled": self.embedder.available,
            "generation_enabled": self.generator.available,
            "rerank_enabled": self.reranker.available,
            "qdrant_enabled": self.vector_store.enabled,
            "redis_enabled": self.resilience.cache.enabled,
            "embedding_model": settings.embedding_model,
            "generation_model": settings.generation_model,
        }

    def prompt_versions(self) -> dict[str, Any]:
        return self.guard.prompt_version()

    # 内部辅助

    def _sanitize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()[: settings.guard_max_input_length]

    def _format_sources(self, hits: list[ScoredChunk]) -> list[dict[str, Any]]:
        return [
            {
                "id": hit.chunk.id,
                "document_id": hit.chunk.document_id,
                "document_name": hit.chunk.document_name,
                "chunk_index": hit.chunk.index,
                "score": round(hit.score, 4),
                "text_preview": hit.chunk.text[:220],
            }
            for hit in hits
        ]

    def _ensure_kb(self, kb_id: str) -> KnowledgeBase:
        kb = self.db.get_kb(kb_id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
        return kb

    def _require_global_role(self, actor: User, minimum: Role) -> None:
        if _ROLE_LEVEL[actor.role] < _ROLE_LEVEL[minimum]:
            raise HTTPException(status_code=403, detail="当前用户没有此操作权限")

    def _require_kb_access(self, actor: User, kb_id: str, minimum: Role) -> None:
        self._ensure_kb(kb_id)
        if actor.role == Role.ADMIN:
            return
        permission = self.db.get_user_kb_permission(actor.id, kb_id)
        if not permission or _ROLE_LEVEL[permission.role] < _ROLE_LEVEL[minimum]:
            raise HTTPException(status_code=403, detail="当前用户没有该知识库的访问权限")

    def _visible_documents(self, actor: User, kb_id: str) -> list[Any]:
        return self.access_control.visible_documents(actor, kb_id)

    def _visible_document_ids(self, actor: User, kb_id: str) -> set[str]:
        return self.access_control.visible_document_ids(actor, kb_id)

    def _require_document_access(self, actor: User, document: Any, minimum: Role) -> None:
        self.access_control.require_document_access(actor, document, minimum)
