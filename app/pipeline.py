from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator
import asyncio
import queue as queue_module
import threading
import secrets
import uuid

from fastapi import HTTPException, status

from .coding_agent import CodingAgent
from .config import settings
from .components import AccessControlComponent, ObservabilityComponent, RetrievalAdjustmentComponent
from .agent import RagAgent
from .feedback import FeedbackWeights
from .observability import StructuredLogger
from .documents import delete_document, ingest_file
from .documents import extract_text
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
from .text import tokenize
from .vector_store import QdrantVectorStore

_ROLE_LEVEL = {
    Role.VIEWER: 1,
    Role.EDITOR: 2,
    Role.ADMIN: 3,
}


_AGENT_TOOLS = {"calculate", "sql_query", "plot_chart", "retrieve"}
_TOOL_STRONG_PATTERNS = [
    "帮我计算", "请计算", "帮我算", "请算", "算一下", "求值",
    "帮我画", "请画", "画一个", "画图", "绘图", "图表", "柱状图", "折线图", "饼图", "散点图",
    "sql", "select", "查询数据库", "查询表", "检索一下", "帮我查", "检索", 
]
_DOC_CONTENT_WORDS = ["文档", "知识库", "资料", "内容", "讲", "总结", "有什么", "介绍"]


def _looks_like_tool_request(question: str) -> bool:
    lowered = question.lower()
    has_expression = bool(re.search(r"[0-9]|[+\-*/=()%]", question))
    if any(word in lowered for word in _DOC_CONTENT_WORDS) and not has_expression:
        return False
    return any(word in lowered for word in _TOOL_STRONG_PATTERNS)


def _choose_tool_heuristic(question: str) -> str:
    lowered = question.lower()
    if re.search(r"select\s", lowered, flags=re.IGNORECASE) or any(word in lowered for word in ["查询数据库", "查询表", "统计文档"]):
        return "sql_query"
    if any(word in lowered for word in ["柱状图", "折线图", "饼图", "散点图", "图表", "画图", "绘图", "画一个", "帮我画", "请画"]):
        return "plot_chart"
    if any(word in lowered for word in ["计算", "等于", "求值"]) and re.search(r"[0-9]|[+\-*/=()%]", question):
        return "calculate"
    return "retrieve"


def _extract_expression(question: str) -> str:
    match = re.search(r"([-+]?\(?[0-9][0-9\s]*(?:[+\-*/()][0-9\s()]+)+\)?)", question)
    return match.group(1).replace(" ", "") if match else ""


def _guess_chart_kind(question: str) -> str:
    lowered = question.lower()
    if "饼图" in lowered or "占比" in lowered:
        return "pie"
    if "散点" in lowered or "分布" in lowered:
        return "scatter"
    if "折线" in lowered or "趋势" in lowered or "变化" in lowered:
        return "line"
    return "bar"


def _to_number_list(value: Any, default: list[float]) -> list[float]:
    if not isinstance(value, list):
        return default
    numbers: list[float] = []
    for item in value[:100]:
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            continue
    return numbers or default


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
        self.coding_agent = CodingAgent(self.generator, self.guard)
        self.feedback_weights = FeedbackWeights(self.db)
        self.access_control = AccessControlComponent(self.db)
        self.retrieval_adjustment = RetrievalAdjustmentComponent(self.feedback_weights)
        self.observability = ObservabilityComponent(self.logger)
        self.pipeline_scheduler = self._build_pipeline_scheduler()
        self._retriever_cache: dict[tuple[str, ...], Retriever] = {}
        self._ensure_admin_user()

        self._upload_queue: "queue_module.Queue[str]" = queue_module.Queue()
        self._upload_tasks: dict[str, dict[str, Any]] = {}
        self._upload_lock = threading.Lock()
        self._upload_worker = threading.Thread(target=self._upload_worker_loop, daemon=True, name="rag-upload-worker")
        self._upload_worker.start()
        self._source_scheduler = threading.Thread(target=self._source_scheduler_loop, daemon=True, name="rag-source-scheduler")
        self._source_scheduler.start()

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
        def history_loader(_: str, session_id: str, user_id: str):
            return self.db.list_messages(session_id, user_id)

        def message_recorder(_: str, session_id: str, question: str, answer: str, user_id: str, sources: list[dict[str, Any]] | None = None):
            self.db.add_message(session_id, "user", question, user_id, [])
            self.db.add_message(session_id, "assistant", answer, user_id, sources)

        def record(kind: str, session_id: str, question: str | None = None, answer: str | None = None, user_id: str | None = None, sources: list[dict[str, Any]] | None = None):
            if kind == "history":
                return history_loader("history", session_id, user_id)
            message_recorder("messages", session_id, question, answer, user_id, sources)

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
        self.ensure_session(actor, session_id)
        agent_cache_key = self._agent_cache_key(kb_id, question, actor.id)
        cached_agent = self._cached_qa(agent_cache_key)
        if cached_agent is not None:
            return {
                "answer": cached_agent["answer"],
                "sources": cached_agent["sources"],
                "route": "agent",
                "cached": True,
                "elapsed_ms": 0,
                "rewritten_query": question,
            }
        if self.route_query(question) == "agent":
            return self._run_agent_pipeline(actor, kb_id, question, session_id, document_id)
        context = PipelineContext(
            actor=actor,
            kb_id=kb_id,
            question=question,
            session_id=session_id,
            top_k=top_k,
            document_id=document_id,
            tags=tags,
        )
        context.state["cache_key"] = self._qa_cache_key(kb_id, question, actor.id)
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
        self.db.add_audit_log(user.id, "login", "user", user.id, user.username)
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
        self.db.add_audit_log(actor.id, "create", "kb", kb.id, name)
        if actor.role != Role.ADMIN:
            self.db.grant_kb_permission(actor.id, kb.id, Role.EDITOR)
        return kb

    def delete_kb(self, actor: User, kb_id: str) -> None:
        self._require_global_role(actor, Role.ADMIN)
        if not self.db.get_kb(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        self.db.delete_kb(kb_id)
        self.db.add_audit_log(actor.id, "delete", "kb", kb_id, "")
        self.vector_store.delete_kb(kb_id)
        self._invalidate_retriever_cache(kb_id)
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
        self.db.add_audit_log(actor.id, "delete", "document", document_id, record.name)
        delete_document(self.db, self.vector_store, record.kb_id, document_id)
        self.db.delete_bm25_tokens(kb_id=record.kb_id, document_id=document_id)
        self._invalidate_retriever_cache(record.kb_id)
        self.resilience.cache.clear()

    def ingest_path_with_mode(self, actor: User, kb_id: str, file_path: Path, tags: list[str] | None = None, access_mode: str = "public") -> dict[str, Any]:
        self._require_kb_access(actor, kb_id, Role.EDITOR)
        guard_result = self.guard.validate_upload(file_path.name, None, file_path.stat().st_size)
        if not guard_result.ok:
            raise HTTPException(status_code=400, detail=guard_result.reason)
        record, chunks = ingest_file_with_mode(self.db, kb_id, file_path, tags, access_mode)
        if chunks:
            self.db.save_bm25_tokens(kb_id, {chunk.id: (record.id, chunk.text, tokenize(chunk.text)) for chunk in chunks})
        self.db.add_audit_log(actor.id, "upload", "document", record.id, record.name)
        if access_mode == "restricted" and actor.role != Role.ADMIN:
            self.db.grant_document_permission(actor.id, record.id, Role.EDITOR)
        if self.embedder.available and chunks:
            try:
                vectors = self.embedder.embed([chunk.text for chunk in chunks])
                self.vector_store.replace_document(kb_id, record.id, [chunk.id for chunk in chunks], vectors, [{"tags": chunk.tags} for chunk in chunks])
            except Exception as exc:
                self.logger.event("qdrant_ingest_failed", kb_id=kb_id, document_id=record.id, error=str(exc))
        self.resilience.cache.clear()
        self._invalidate_retriever_cache(kb_id)
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

    def submit_ingestion(self, actor: User, kb_id: str, file_path: Path, tags: list[str] | None = None, access_mode: str = "public", kind: str = "document", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        with self._upload_lock:
            self._upload_tasks[task_id] = {
                "task_id": task_id,
                "status": "pending",
                "actor": actor,
                "kb_id": kb_id,
                "file_path": str(file_path),
                "tags": tags,
                "access_mode": access_mode,
                "kind": kind,
                "extra": extra,
                "result": None,
                "error": None,
                "code": None,
            }
            if len(self._upload_tasks) > 100:
                oldest = next(iter(self._upload_tasks))
                self._upload_tasks.pop(oldest, None)
        self._upload_queue.put(task_id)
        return {"task_id": task_id, "status": "pending"}

    def get_upload_task(self, task_id: str) -> dict[str, Any] | None:
        with self._upload_lock:
            task = self._upload_tasks.get(task_id)
            if task is None:
                return None
            return {
                "task_id": task["task_id"],
                "status": task["status"],
                "result": task["result"],
                "error": task["error"],
                "code": task["code"],
            }

    def _upload_worker_loop(self) -> None:
        while True:
            task_id = self._upload_queue.get()
            with self._upload_lock:
                task = self._upload_tasks.get(task_id)
            if task is None:
                continue
            try:
                with self._upload_lock:
                    task["status"] = "running"
                kind = task.get("kind", "document")
                if kind == "batch":
                    from .batch_import import import_tabular
                    result = import_tabular(self, task["actor"], task["kb_id"], Path(task["file_path"]), task["tags"], (task.get("extra") or {}).get("mode", "document"))
                elif kind == "folder":
                    from .batch_import import index_folder
                    result = index_folder(self, task["actor"], task["kb_id"], Path(task["file_path"]), task["tags"])
                else:
                    result = self.ingest_path_with_mode(
                        task["actor"],
                        task["kb_id"],
                        Path(task["file_path"]),
                        task["tags"],
                        task["access_mode"],
                    )
                with self._upload_lock:
                    task["status"] = "done"
                    task["result"] = result
            except FileExistsError as exc:
                with self._upload_lock:
                    task["status"] = "error"
                    task["error"] = str(exc)
                    task["code"] = 409
                Path(task["file_path"]).unlink(missing_ok=True)
            except Exception as exc:
                with self._upload_lock:
                    task["status"] = "error"
                    task["error"] = f"文档解析失败：{exc}"
                    task["code"] = 400
                Path(task["file_path"]).unlink(missing_ok=True)

    def list_visible_documents(self, actor: User, kb_id: str) -> list[Any]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        return [record.__dict__ for record in self._visible_documents(actor, kb_id)]

    def get_document_content(self, actor: User, document_id: str) -> dict[str, Any]:
        record = self.db.get_document(document_id)
        if not record:
            raise HTTPException(status_code=404, detail="文档不存在")
        self._require_document_access(actor, record, Role.VIEWER)
        text = extract_text(Path(record.file_path))
        return {"id": record.id, "name": record.name, "text": text}

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
        persisted = self.db.get_bm25_tokens(kb_id)
        return Retriever(chunks=chunks, kb_id=kb_id, vector_store=self.vector_store, embedder=self.embedder, persisted_tokens=persisted)

    def _invalidate_retriever_cache(self, kb_id: str | None = None) -> None:
        if kb_id is None:
            self._retriever_cache.clear()
            return
        for key in list(self._retriever_cache):
            if key[0] == kb_id:
                self._retriever_cache.pop(key, None)

    def get_retriever_for_documents(self, kb_id: str, document_ids: list[str]) -> Retriever:
        key = (kb_id, *sorted(document_ids))
        if key in self._retriever_cache:
            return self._retriever_cache[key]
        chunks = self.db.list_chunks_by_documents(kb_id, document_ids)
        persisted = self.db.get_bm25_tokens(kb_id, [chunk.id for chunk in chunks])
        retriever = Retriever(chunks=chunks, kb_id=kb_id, vector_store=self.vector_store, embedder=self.embedder, persisted_tokens=persisted)
        self._retriever_cache[key] = retriever
        return retriever

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
        if weighted and weighted[0].score < 0.3:
            self.db.add_retrieval_gap(kb_id, actor.id, query, weighted[0].score)
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
        cache_key = self._qa_cache_key(kb_id, question, actor.id)

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
            history = self.db.list_messages(session_id, actor.id)
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

        self.db.add_message(session_id, "user", question, actor.id, [])
        self.db.add_message(session_id, "assistant", answer, actor.id, sources)
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
        self.ensure_session(actor, session_id)
        yield {"type": "progress", "stage": "guard", "message": "安全校验中..."}
        guard_result = self.guard.validate_input(question)
        if not guard_result.ok:
            yield {"type": "error", "reason": guard_result.reason}
            return

        question = self._sanitize(question)
        cache_key = self._qa_cache_key(kb_id, question, actor.id)
        cached = self._cached_qa(cache_key)
        if cached is not None:
            yield {"type": "sources", "sources": cached["sources"]}
            yield {"type": "token", "content": cached["answer"]}
            yield {"type": "done", "rewritten_query": question}
            return

        agent_cache_key = self._agent_cache_key(kb_id, question, actor.id)
        cached_agent = self._cached_qa(agent_cache_key)
        if cached_agent is not None:
            yield {"type": "sources", "sources": cached_agent["sources"]}
            yield {"type": "token", "content": cached_agent["answer"]}
            yield {"type": "done", "rewritten_query": question}
            return
        route = await asyncio.to_thread(self.route_query, question)
        if route == "agent":
            async for event in self._stream_agent(actor, kb_id, question, session_id, document_id):
                yield event
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
            history = self.db.list_messages(session_id, actor.id)
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
        if chunks and ("无法回答" in answer or "没有找到" in answer or "没有检索到" in answer):
            answer = fallback_answer(question, chunks)
            yield {"type": "token", "content": answer}
        yield {"type": "progress", "stage": "output_guard", "message": "安全审核中..."}
        output_guard = self.guard.validate_output(question, answer)
        if not output_guard.ok:
            answer = "回答被安全策略拦截，请尝试更具体或合规的问题。"
            yield {"type": "token", "content": answer}

        self.db.add_message(session_id, "user", question, actor.id, [])
        self.db.add_message(session_id, "assistant", answer, actor.id, sources)
        self._cache_qa(cache_key, answer, sources)
        yield {"type": "done", "rewritten_query": rewritten}

    def feedback(self, session_id: str, question: str, answer: str, rating: str, kb_id: str | None = None, document_ids: list[str] | None = None) -> None:
        from .feedback import record_feedback
        record_feedback(self.db, session_id, question, answer, rating, kb_id=kb_id, document_ids=document_ids)

    def list_session_messages(self, actor: User, session_id: str) -> list[dict[str, str]]:
        return self.db.list_messages(session_id, actor.id, limit=100)

    def regenerate_answer(self, actor: User, kb_id: str, question: str, session_id: str, top_k: int, document_id: str | None, tags: list[str] | None) -> dict[str, Any]:
        return self.query_pipeline(actor, kb_id, question, session_id, top_k, document_id, tags)

    def suggest_questions(self, question: str, answer: str) -> list[str]:
        if not self.generator.available:
            return []
        try:
            rendered = self.prompts.render("query_rewrite", {"query": question})
            messages = [
                {"role": "system", "content": "根据用户问题和回答，给出 3 个自然的中文追问问题。只输出问题，每行一个。"},
                {"role": "user", "content": f"问题：{question}\n回答：{answer}"},
            ]
            text = self.generator.generate(messages, temperature=0.2).strip()
            return [line.strip() for line in text.splitlines() if line.strip()][:3]
        except Exception:
            return []

    def ensure_session(self, actor: User, session_id: str) -> None:
        sessions = self.db.list_sessions(actor.id)
        if not any(item["id"] == session_id for item in sessions):
            self.db.create_session(session_id, actor.id)

    def list_sessions(self, actor: User) -> list[dict[str, Any]]:
        return self.db.list_sessions(actor.id)

    def create_session(self, actor: User, session_id: str | None = None, title: str = "新会话") -> dict[str, Any]:
        session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
        self.db.create_session(session_id, actor.id, title)
        return {"id": session_id, "title": title}

    def rename_session(self, actor: User, session_id: str, title: str) -> None:
        self.db.rename_session(session_id, actor.id, title)

    def summarize_session(self, actor: User, session_id: str) -> str:
        messages = self.db.list_messages(session_id, actor.id, limit=50)
        if not self.generator.available or not messages:
            return ""
        try:
            transcript = "".join(f"{m['role']}: {m['content'][:500]}" for m in messages[-20:])
            text = self.generator.generate([
                {"role": "system", "content": "用 3 句话总结这段对话，只输出总结。"},
                {"role": "user", "content": transcript},
            ], temperature=0.2)
            self.db.update_session_summary(session_id, actor.id, text.strip())
            return text.strip()
        except Exception:
            return ""

    def delete_session(self, actor: User, session_id: str) -> None:
        self.db.delete_session(session_id, actor.id)
        self.db.delete_session_messages(session_id, actor.id)

    def clear_session_messages(self, actor: User, session_id: str) -> None:
        self.db.delete_session_messages(session_id, actor.id)

    def delete_message(self, actor: User, message_id: int) -> None:
        message = self.db.get_message(message_id)
        if not message or message.get("user_id") != actor.id:
            raise HTTPException(status_code=404, detail="消息不存在")
        self.db.delete_message(message_id, actor.id)
    # 缓存

    def _qa_cache_key(self, kb_id: str, question: str, user_id: str) -> str:
        digest = hashlib.sha256(f"{kb_id}:{user_id}:{question}".encode("utf-8")).hexdigest()
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

    def health(self) -> dict[str, Any]:
        result = self.capabilities()
        result["redis_ok"] = True
        if self.resilience.cache.enabled:
            try:
                self.resilience.cache.set("health:check", "1", 5)
                result["redis_ok"] = self.resilience.cache.get("health:check") == "1"
            except Exception:
                result["redis_ok"] = False
        result["qdrant_ok"] = True
        if self.vector_store.enabled:
            try:
                self.vector_store._get_client().get_collections()
            except Exception:
                result["qdrant_ok"] = False
        return result

    def list_retrieval_gaps(self, actor: User, kb_id: str | None = None) -> list[dict[str, Any]]:
        self._require_global_role(actor, Role.ADMIN)
        return self.db.list_retrieval_gaps(kb_id)

    def retrieval_gap_summary(self, actor: User) -> dict[str, Any]:
        self._require_global_role(actor, Role.ADMIN)
        gaps = self.db.list_retrieval_gaps(limit=500)
        grouped: dict[str, int] = {}
        for gap in gaps:
            grouped[gap["question"]] = grouped.get(gap["question"], 0) + 1
        top = sorted(grouped.items(), key=lambda item: item[1], reverse=True)[:10]
        return {"total": len(gaps), "unresolved": sum(1 for item in gaps if not item["resolved"]), "top": [{"question": q, "count": c} for q, c in top]}

    def prompt_versions(self) -> dict[str, Any]:
        return self.guard.prompt_version()

    def route_query(self, question: str) -> str:
        if self.generator.available:
            try:
                rendered = self.prompts.render("router", {"question": question})
                text = self.generator.generate(
                    [
                        {"role": "system", "content": rendered["system"]},
                        {"role": "user", "content": rendered["user"]},
                    ],
                    temperature=0.0,
                ).strip().lower()
                if "agent" in text:
                    if _looks_like_tool_request(question):
                        return "agent"
                if "rag" in text:
                    return "rag"
            except Exception:
                pass
        return "agent" if _looks_like_tool_request(question) else "rag"


    def run_agent_tool(self, name: str, args: dict[str, Any], actor: User | None = None) -> dict[str, Any]:
        from .agent_runner import run_tool
        return run_tool(name, args, service=self, actor=actor)

    # 企业功能

    def create_api_key(self, actor: User, name: str) -> dict[str, Any]:
        self._require_global_role(actor, Role.ADMIN)
        from .security import hash_api_key
        raw = "rag_" + secrets.token_urlsafe(24)
        record = self.db.create_api_key(name.strip() or "default", actor.id, hash_api_key(raw))
        return {"id": record["id"], "name": record["name"], "key": raw}

    def list_api_keys(self, actor: User) -> list[dict[str, Any]]:
        return self.db.list_api_keys(actor.id)

    def revoke_api_key(self, actor: User, key_id: str) -> None:
        self.db.revoke_api_key(key_id, actor.id)

    def change_password(self, actor: User, old_password: str, new_password: str) -> None:
        from .security import hash_password, verify_password
        if len(new_password) < 8:
            raise HTTPException(status_code=422, detail="新密码至少需要 8 个字符")
        if not verify_password(old_password, actor.password_hash):
            raise HTTPException(status_code=400, detail="原密码不正确")
        self.db.update_user(actor.id, password_hash=hash_password(new_password))

    def reset_password(self, actor: User, user_id: str, new_password: str) -> None:
        self._require_global_role(actor, Role.ADMIN)
        if len(new_password) < 8:
            raise HTTPException(status_code=422, detail="新密码至少需要 8 个字符")
        self.db.update_user(user_id, password_hash=hash_password(new_password))

    def create_source(self, actor: User, kb_id: str, kind: str, name: str, config: dict[str, Any], interval_minutes: int = 60) -> dict[str, Any]:
        self._require_kb_access(actor, kb_id, Role.EDITOR)
        if kind not in {"rss", "db", "api"}:
            raise HTTPException(status_code=422, detail="数据源类型只能是 rss/db/api")
        return self.db.create_source(kb_id, kind, name, config, interval_minutes)

    def list_sources(self, actor: User, kb_id: str) -> list[dict[str, Any]]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        return self.db.list_sources(kb_id)

    def update_source(self, actor: User, source_id: str, **fields: Any) -> None:
        source = next((item for item in self.db.list_sources() if item.get("id") == source_id), None)
        if not source:
            raise HTTPException(status_code=404, detail="数据源不存在")
        self._require_kb_access(actor, source["kb_id"], Role.EDITOR)
        self.db.update_source(source_id, **fields)

    def delete_source(self, actor: User, source_id: str) -> None:
        source = next((item for item in self.db.list_sources() if item.get("id") == source_id), None)
        if not source:
            raise HTTPException(status_code=404, detail="数据源不存在")
        self._require_kb_access(actor, source["kb_id"], Role.EDITOR)
        self.db.delete_source(source_id)

    def sync_source_now(self, actor: User, source_id: str) -> dict[str, Any]:
        source = next((item for item in self.db.list_sources() if item.get("id") == source_id), None)
        if not source:
            raise HTTPException(status_code=404, detail="数据源不存在")
        self._require_kb_access(actor, source["kb_id"], Role.EDITOR)
        from .sources import sync_source
        result = sync_source(self, actor, source)
        self.db.mark_source_synced(source_id)
        return result

    def _source_scheduler_loop(self) -> None:
        from datetime import datetime, timezone
        while True:
            try:
                for source in self.db.list_sources():
                    if not source.get("enabled"):
                        continue
                    interval = max(int(source.get("interval_minutes") or 60), 1)
                    last = source.get("last_synced_at")
                    if last:
                        try:
                            last_at = datetime.fromisoformat(last)
                            if (datetime.now(timezone.utc) - last_at).total_seconds() < interval * 60:
                                continue
                        except ValueError:
                            pass
                    try:
                        from .sources import sync_source
                        admin = self.db.get_user_by_username(settings.admin_username) or self.db.get_user_by_username("korce")
                        if admin:
                            sync_source(self, admin, source)
                            self.db.mark_source_synced(source["id"])
                    except Exception as exc:
                        self.logger.event("source_sync_failed", source_id=source.get("id"), error=str(exc))
            except Exception:
                pass
            time.sleep(60)

    def generate_kb_toc(self, actor: User, kb_id: str) -> dict[str, Any]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        documents = self.db.list_documents(kb_id)
        toc = [{"id": doc.id, "title": doc.name} for doc in documents]
        overview = self._summarize_knowledge_base(documents)
        if self.generator.available and documents:
            try:
                names = "\n".join(f"- {doc.name}" for doc in documents)
                text = self.generator.generate(
                    [
                        {"role": "system", "content": "根据文档清单生成知识库目录。每行格式：- 标题；不要输出概述或其他文字。"},
                        {"role": "user", "content": names},
                    ],
                    temperature=0.2,
                )
                parsed = [line.strip().lstrip("- ").strip() for line in text.splitlines() if line.strip() and not line.startswith("概述")]
                if parsed:
                    toc = [{"id": "", "title": title} for title in parsed]
            except Exception:
                pass
        self.db.set_kb_toc(kb_id, toc, overview)
        return {"toc": toc, "overview": overview}
    def get_kb_toc(self, actor: User, kb_id: str) -> dict[str, Any]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        toc, overview = self.db.get_kb_toc(kb_id)
        return {"toc": toc, "overview": overview}

    def analytics_questions(self, actor: User, kb_id: str) -> list[dict[str, Any]]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        grouped: dict[str, dict[str, Any]] = {}
        for gap in self.db.list_retrieval_gaps(kb_id):
            question = gap["question"]
            entry = grouped.setdefault(question, {"question": question, "count": 0, "unresolved": 0, "last_asked": ""})
            entry["count"] += 1
            entry["unresolved"] += 0 if gap["resolved"] else 1
            entry["last_asked"] = max(entry["last_asked"], gap["created_at"])
        return sorted(grouped.values(), key=lambda item: item["count"], reverse=True)

    def analytics_report(self, actor: User, kb_id: str) -> dict[str, Any]:
        questions = self.analytics_questions(actor, kb_id)
        return {
            "total_questions": sum(item["count"] for item in questions),
            "unresolved": sum(item["unresolved"] for item in questions),
            "top": questions[:10],
        }

    def export_analytics_report(self, actor: User, kb_id: str) -> str:
        report = self.analytics_report(actor, kb_id)
        lines = ["问题,次数,未解决,最后提问时间"]
        for item in report["top"]:
            lines.append(f"{item['question']},{item['count']},{item['unresolved']},{item['last_asked']}")
        return "\n".join(lines)

    def analytics_cards(self, actor: User, kb_id: str) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for item in self.analytics_questions(actor, kb_id):
            card_id = hashlib.sha256(item["question"].encode("utf-8")).hexdigest()[:12]
            cards.append({
                "id": card_id,
                "question": item["question"],
                "count": item["count"],
                "unresolved": item["unresolved"],
                "last_asked": item["last_asked"],
                "summary": self._card_summary(item),
            })
        return cards

    def _card_summary(self, item: dict[str, Any]) -> str:
        fallback = f"该问题累计被提问 {item['count']} 次，其中 {item['unresolved']} 次未找到满意资料，建议补充相关文档。"
        if not self.generator.available:
            return fallback
        try:
            text = self.generator.generate(
                [
                    {"role": "system", "content": "根据问题统计信息生成一句 50 字以内的中文分析摘要，不要编造数据。"},
                    {"role": "user", "content": f"问题：{item['question']}\n次数：{item['count']}\n未解决：{item['unresolved']}"},
                ],
                temperature=0.2,
            ).strip()
            return text or fallback
        except Exception:
            return fallback

    def export_analytics_card(self, actor: User, kb_id: str, card_id: str) -> str:
        card = next((item for item in self.analytics_cards(actor, kb_id) if item["id"] == card_id), None)
        if not card:
            raise HTTPException(status_code=404, detail="分析卡片不存在")
        return (
            "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>分析卡片</title></head><body style=\"font-family:'Microsoft YaHei',sans-serif;background:#f5f7fa;margin:0;padding:24px;\">"
            "<div style=\"max-width:520px;margin:0 auto;background:#fff;border:1px solid #e4e7ed;border-radius:8px;padding:24px;\">"
            f"<h2 style=\"margin-top:0;\">问题分组分析</h2>"
            f"<div style=\"color:#606266;font-size:15px;margin:8px 0;\">{card['question']}</div>"
            f"<div style=\"display:flex;gap:24px;margin:16px 0;\">"
            f"<div><div style=\"font-size:28px;font-weight:700;\">{card['count']}</div><div style=\"color:#909399;\">提问次数</div></div>"
            f"<div><div style=\"font-size:28px;font-weight:700;color:#f56c6c;\">{card['unresolved']}</div><div style=\"color:#909399;\">未解决</div></div>"
            f"</div><div style=\"color:#606266;line-height:1.7;\">{card['summary']}</div>"
            f"<div style=\"color:#c0c4cc;font-size:12px;margin-top:16px;\">最后提问：{card['last_asked']}</div>"
            "</div></body></html>"
        )

    def generate_kb_overview(self, actor: User, kb_id: str) -> dict[str, Any]:
        self._require_kb_access(actor, kb_id, Role.VIEWER)
        documents = self.db.list_documents(kb_id)
        overview = self._summarize_knowledge_base(documents)
        toc, _ = self.db.get_kb_toc(kb_id)
        self.db.set_kb_toc(kb_id, toc, overview)
        return {"overview": overview}

    def _summarize_knowledge_base(self, documents: list[Any]) -> str:
        fallback = f"知识库共包含 {len(documents)} 个文档。"
        if not self.generator.available or not documents:
            return fallback
        try:
            names = "\n".join(f"- {doc.name}" for doc in documents[:20])
            text = self.generator.generate(
                [
                    {"role": "system", "content": "根据文档清单生成一段 100 字以内的中文知识库概述，只输出概述内容，不要输出其他文字。"},
                    {"role": "user", "content": names},
                ],
                temperature=0.2,
            ).strip()
            for prefix in ("概述：", "概述:", "概述"):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
            return text or fallback
        except Exception:
            return fallback

    def run_coding_agent(self, actor: User, task: str, project_path: str, max_steps: int = 8) -> dict[str, Any]:
        self._require_global_role(actor, Role.EDITOR)
        return self.coding_agent.run(task, project_path, max_steps)

    # 内部辅助

    def _agent_cache_key(self, kb_id: str, question: str, user_id: str) -> str:
        digest = hashlib.sha256(f"agent:{kb_id}:{user_id}:{question}".encode("utf-8")).hexdigest()
        return f"agent:{digest}"

    def _run_agent_pipeline(self, actor: User, kb_id: str, question: str, session_id: str, document_id: str | None = None) -> dict[str, Any]:
        started = time.time()
        cache_key = self._agent_cache_key(kb_id, question, actor.id)
        tool_calls: list[dict[str, Any]] = []
        answer = ""
        reason = "ok"
        try:
            answer, tool_calls = self._react_agent(actor, question, kb_id, document_id)
        except Exception as exc:
            answer = f"工具执行失败：{exc}"
            reason = "tool_error"
        output_guard = self.guard.validate_output(question, answer)
        if not output_guard.ok:
            answer = "回答被安全策略拦截，请尝试更具体或合规的问题。"
        self.db.add_message(session_id, "user", question, actor.id, [])
        self.db.add_message(session_id, "assistant", answer, actor.id, [])
        self._cache_qa(cache_key, answer, [])
        self.logger.event("agent_pipeline_done", kb_id=kb_id, tool_calls=len(tool_calls), reason=reason)
        return {
            "answer": answer,
            "sources": [],
            "route": "agent",
            "rewritten_query": question,
            "elapsed_ms": (time.time() - started) * 1000,
            "attempts": len(tool_calls),
            "check_reason": reason,
            "tools": [item.get("tool") for item in tool_calls],
            "tool": tool_calls[-1].get("tool") if tool_calls else "none",
        }

    async def _stream_agent(self, actor: User, kb_id: str, question: str, session_id: str, document_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        cache_key = self._agent_cache_key(kb_id, question, actor.id)
        cached = self._cached_qa(cache_key)
        if cached is not None:
            yield {"type": "sources", "sources": cached["sources"]}
            yield {"type": "token", "content": cached["answer"]}
            yield {"type": "done", "rewritten_query": question}
            return
        yield {"type": "progress", "stage": "agent_plan", "message": "规划工具中..."}
        transcript: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        final_text = ""
        final_from_llm = False
        for step in range(1, 6):
            decision = await asyncio.to_thread(self._react_decision, question, transcript)
            if "final" in decision:
                final_text = str(decision.get("final") or "").strip()
                final_from_llm = bool(final_text)
                break
            tool_name = str(decision.get("tool") or "")
            args = decision.get("args") or {}
            if tool_name not in _AGENT_TOOLS:
                final_text = f"未知工具：{tool_name}"
                final_from_llm = True
                break
            args = self._sanitize_tool_args(tool_name, args, question, kb_id, document_id)
            yield {"type": "progress", "stage": "agent_execute", "message": f"第 {step} 步执行 {tool_name}..."}
            try:
                tool_result = await asyncio.to_thread(self.run_agent_tool, tool_name, args, actor)
                observation = self._format_tool_output(tool_name, tool_result)
            except Exception as exc:
                observation = f"工具执行失败：{exc}"
            transcript.append(f"第 {step} 步调用 {tool_name}：\n{observation}")
            tool_calls.append({"tool": tool_name, "args": args})
        else:
            final_text = self._compose_agent_answer(question, "\n\n".join(transcript))
        if not final_text:
            final_text = self._compose_agent_answer(question, "\n\n".join(transcript))
        yield {"type": "progress", "stage": "generation", "message": "生成回答中..."}
        answer_parts: list[str] = []
        if final_from_llm:
            answer_parts = [final_text]
            yield {"type": "token", "content": final_text}
        elif self.generator.available:
            messages = self._tool_answer_messages(question, final_text)
            try:
                async for token in self.generator.stream(messages):
                    answer_parts.append(token)
                    yield {"type": "token", "content": token}
            except Exception:
                answer_parts = [final_text]
                yield {"type": "token", "content": final_text}
        else:
            answer_parts = [final_text]
            yield {"type": "token", "content": final_text}
        answer = "".join(answer_parts)
        yield {"type": "progress", "stage": "output_guard", "message": "安全审核中..."}
        output_guard = self.guard.validate_output(question, answer)
        if not output_guard.ok:
            answer = "回答被安全策略拦截，请尝试更具体或合规的问题。"
            yield {"type": "token", "content": answer}
        self.db.add_message(session_id, "user", question, actor.id, [])
        self.db.add_message(session_id, "assistant", answer, actor.id, [])
        self._cache_qa(cache_key, answer, [])
        yield {"type": "done", "rewritten_query": question}

    def _react_agent(self, actor: User, question: str, kb_id: str, document_id: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        transcript: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for step in range(1, 6):
            decision = self._react_decision(question, transcript)
            if decision.get("final"):
                final_text = str(decision.get("final") or "").strip()
                if final_text:
                    return final_text, tool_calls
                break
            tool_name = str(decision.get("tool") or "")
            args = decision.get("args") or {}
            if tool_name not in _AGENT_TOOLS:
                return f"未知工具：{tool_name}", tool_calls
            args = self._sanitize_tool_args(tool_name, args, question, kb_id, document_id)
            try:
                result = self.run_agent_tool(tool_name, args, actor=actor)
                observation = self._format_tool_output(tool_name, result)
            except Exception as exc:
                observation = f"工具执行失败：{exc}"
            transcript.append(f"第 {step} 步调用 {tool_name}：\n{observation}")
            tool_calls.append({"tool": tool_name, "args": args, "observation": observation})
        return self._compose_agent_answer(question, "\n\n".join(transcript)), tool_calls

    def _react_decision(self, question: str, transcript: list[str]) -> dict[str, Any]:
        if not self.generator.available:
            if transcript:
                return {"final": ""}
            return {"tool": _choose_tool_heuristic(question), "args": {}}
        system = (
            "你是工具调用智能体。根据用户问题和已有工具结果，决定下一步。"
            "可用工具：calculate（参数 expression）、sql_query（参数 query）、"
            "plot_chart（参数 kind/x/y/title）、retrieve（参数 query/top_k）。"
            "如果已经得到答案，输出 {\"final\": \"最终回答\"}；如果需要继续调用工具，"
            "输出 {\"tool\": \"工具名\", \"args\": {...}}。只输出 JSON，不要解释。"
        )
        parts = [f"用户问题：{question}"]
        if transcript:
            parts.append("已有工具结果：\n" + "\n\n".join(transcript))
        parts.append("请决定下一步。")
        text = self.generator.generate(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            temperature=0.0,
        )
        decision = self._parse_json_object(text)
        if not isinstance(decision, dict):
            if not transcript:
                return {"tool": _choose_tool_heuristic(question), "args": {}}
            return {"final": ""}
        if "final" in decision:
            return {"final": str(decision.get("final") or "").strip()}
        return decision
    def _plan_agent_tool(self, question: str, kb_id: str, document_id: str | None = None) -> tuple[str, dict[str, Any]]:
        tool_name = _choose_tool_heuristic(question)
        args: dict[str, Any] = {}
        if self.generator.available:
            try:
                plan = self._llm_plan_tool(question)
                if plan and plan.get("tool") in _AGENT_TOOLS:
                    tool_name = plan["tool"]
                    args = plan.get("args") or {}
            except Exception:
                pass
        return tool_name, self._sanitize_tool_args(tool_name, args, question, kb_id, document_id)

    def _llm_plan_tool(self, question: str) -> dict[str, Any]:
        system = (
            "你是工具调用规划器，只输出 JSON，不要解释。" 
            "可用工具：calculate（参数 expression 数学表达式）、sql_query（参数 query 只读 SQL）、" 
            "plot_chart（参数 kind 为 bar/line/pie/scatter，x、y 为数字列表，title 标题）、" 
            "retrieve（参数 query 检索词、top_k 数量）。" 
            "输出格式：{\"tool\": \"工具名\", \"args\": {...}}"
        )
        text = self.generator.generate(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
            temperature=0.0,
        )
        plan = self._parse_json_object(text)
        if not isinstance(plan, dict):
            raise ValueError("工具规划结果不是 JSON")
        return plan

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    def _sanitize_tool_args(self, tool_name: str, args: dict[str, Any], question: str, kb_id: str, document_id: str | None = None) -> dict[str, Any]:
        args = args or {}
        if tool_name == "calculate":
            expression = str(args.get("expression") or _extract_expression(question) or "").strip()
            return {"expression": expression[:500]}
        if tool_name == "sql_query":
            query = str(args.get("query") or question or "").strip()
            return {"query": query[:1000]}
        if tool_name == "plot_chart":
            kind = str(args.get("kind") or _guess_chart_kind(question) or "line").lower()
            if kind not in {"bar", "line", "pie", "scatter"}:
                kind = "line"
            default_x = [1.0, 2.0, 3.0, 4.0]
            default_y = [3.0, 5.0, 2.0, 7.0]
            x = _to_number_list(args.get("x"), default_x)
            y = _to_number_list(args.get("y"), default_y)
            size = min(len(x), len(y))
            if size == 0:
                x, y = default_x, default_y
            else:
                x, y = x[:size], y[:size]
            return {
                "kind": kind,
                "x": x,
                "y": y,
                "title": str(args.get("title") or question[:30] or "chart"),
            }
        return {
            "kb_id": kb_id,
            "query": str(args.get("query") or question or "").strip()[:500],
            "top_k": max(1, min(int(args.get("top_k") or 5), 20)),
            "document_id": document_id,
        }

    def _format_tool_output(self, tool_name: str, result: dict[str, Any]) -> str:
        if tool_name == "calculate":
            return f"计算结果：{result.get('result')}"
        if tool_name == "sql_query":
            return "查询结果：" + json.dumps(result.get("result"), ensure_ascii=False)
        if tool_name == "plot_chart":
            return f"已生成图表：{result.get('path')}"
        items = result.get("result") or []
        if not items:
            return "没有检索到相关资料。"
        return "\n\n".join(
            f"[{index + 1}] {item.get('document_name')}（score={item.get('score')}）\n{item.get('text')}"
            for index, item in enumerate(items[:10])
        )

    def _tool_answer_messages(self, question: str, tool_text: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": "你是知识库助手。根据用户问题和工具执行结果，给出简洁、自然的中文回答；不要编造工具未提供的数据。",
            },
            {
                "role": "user",
                "content": f"问题：{question}\n工具结果：\n{tool_text}",
            },
        ]

    def _compose_agent_answer(self, question: str, tool_text: str) -> str:
        if not self.generator.available:
            return tool_text
        try:
            answer = self.generator.generate(self._tool_answer_messages(question, tool_text), temperature=0.2).strip()
            return answer or tool_text
        except Exception:
            return tool_text

    def _sanitize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()[: settings.guard_max_input_length]

    def _format_sources(self, hits: list[ScoredChunk]) -> list[dict[str, Any]]:
        return [
            {
                "id": hit.chunk.id,
                "document_id": hit.chunk.document_id,
                "document_name": hit.chunk.document_name,
                "chunk_index": hit.chunk.index,
                "context_index": index + 1,
                "score": round(hit.score, 4),
                "text_preview": hit.chunk.text[:220],
            }
            for index, hit in enumerate(hits, start=1)
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









