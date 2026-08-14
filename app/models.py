from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    role: Role
    is_active: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role.value if isinstance(self.role, Role) else self.role,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }


@dataclass
class KnowledgeBasePermission:
    id: str
    user_id: str
    kb_id: str
    role: Role
    created_at: str = ""


@dataclass
class DocumentPermission:
    id: str
    user_id: str
    document_id: str
    role: Role
    created_at: str = ""


@dataclass
class DocumentShare:
    id: str
    document_id: str
    user_id: str
    created_by: str
    created_at: str = ""


@dataclass
class KnowledgeBase:
    id: str
    name: str
    description: str = ""
    created_at: str = ""


@dataclass
class DocumentRecord:
    id: str
    kb_id: str
    name: str
    file_path: str
    content_hash: str
    status: str = "ready"
    access_mode: str = "public"
    created_at: str = ""


@dataclass
class Chunk:
    id: str
    kb_id: str
    document_id: str
    document_name: str
    text: str
    index: int
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kb_id": self.kb_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "text": self.text,
            "index": self.index,
            "tags": self.tags,
        }


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    dense_score: float | None = None
    bm25_score: float | None = None


@dataclass
class QueryResult:
    answer: str
    sources: list[dict[str, Any]]
    route: str
    rewritten_query: str = ""
    elapsed_ms: float = 0
