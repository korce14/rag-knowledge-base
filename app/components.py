from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from .agent import RagAgent
from .feedback import FeedbackWeights
from .models import Role, ScoredChunk, User
from .observability import StructuredLogger
from .storage import Database

_ROLE_LEVEL = {
    Role.VIEWER: 1,
    Role.EDITOR: 2,
    Role.ADMIN: 3,
}


class AccessControlComponent:
    def __init__(self, db: Database):
        self.db = db

    def require_global_role(self, actor: User, minimum: Role) -> None:
        if _ROLE_LEVEL[actor.role] < _ROLE_LEVEL[minimum]:
            raise HTTPException(status_code=403, detail="当前用户没有此操作权限")

    def require_kb_access(self, actor: User, kb_id: str, minimum: Role) -> None:
        if not self.db.get_kb(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        if actor.role == Role.ADMIN:
            return
        permission = self.db.get_user_kb_permission(actor.id, kb_id)
        if not permission or _ROLE_LEVEL[permission.role] < _ROLE_LEVEL[minimum]:
            raise HTTPException(status_code=403, detail="当前用户没有该知识库的访问权限")

    def require_document_access(self, actor: User, document: Any, minimum: Role) -> None:
        if actor.role == Role.ADMIN:
            return
        if document.access_mode == "public":
            self.require_kb_access(actor, document.kb_id, minimum)
            return
        shared_ids = set(self.db.list_shared_document_ids(actor.id))
        if document.id in shared_ids and _ROLE_LEVEL[minimum] <= _ROLE_LEVEL[Role.VIEWER]:
            return
        permission = self.db.get_user_document_permission(actor.id, document.id)
        if not permission or _ROLE_LEVEL[permission.role] < _ROLE_LEVEL[minimum]:
            raise HTTPException(status_code=403, detail="当前用户没有该文档的访问权限")


        if actor.role == Role.ADMIN:
            return self.db.list_documents(kb_id)
        shared_ids = set(self.db.list_shared_document_ids(actor.id))
        visible: list[Any] = []
        for document in self.db.list_documents(kb_id):
            if document.access_mode == "public":
                visible.append(document)
            elif self.db.get_user_document_permission(actor.id, document.id) or document.id in shared_ids:
                visible.append(document)
        return visible
    def visible_document_ids(self, actor: User, kb_id: str) -> set[str]:
        return {document.id for document in self.visible_documents(actor, kb_id)}


class RetrievalAdjustmentComponent:
    def __init__(self, feedback_weights: FeedbackWeights):
        self.feedback_weights = feedback_weights

    def apply_weights(self, kb_id: str, hits: list[ScoredChunk]) -> list[ScoredChunk]:
        return self.feedback_weights.apply(kb_id, hits)


class ObservabilityComponent:
    def __init__(self, logger: StructuredLogger):
        self.logger = logger

    def event(self, event: str, **data: Any) -> None:
        self.logger.event(event, **data)
