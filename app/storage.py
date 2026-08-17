from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings
from .models import Chunk, DocumentRecord, KnowledgeBase, KnowledgeBasePermission, Role, User
from .models import DocumentPermission
from .models import DocumentShare


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """SQLite 元数据存储，保存知识库、文档、用户和权限。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.data_dir / "knowledge.db")
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        with self.connection() as conn:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    access_mode TEXT NOT NULL DEFAULT 'public',
                    created_at TEXT NOT NULL,
                    UNIQUE(kb_id, content_hash)
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    document_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_kb ON chunks(kb_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_kb_permissions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, kb_id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_kb_permissions_kb ON user_kb_permissions(kb_id);
                CREATE INDEX IF NOT EXISTS idx_user_kb_permissions_user ON user_kb_permissions(user_id);

                CREATE TABLE IF NOT EXISTS user_document_permissions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, document_id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_document_permissions_doc ON user_document_permissions(document_id);
                CREATE INDEX IF NOT EXISTS idx_user_document_permissions_user ON user_document_permissions(user_id);

                CREATE TABLE IF NOT EXISTS document_shares (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_document_shares_doc ON document_shares(document_id);
                CREATE INDEX IF NOT EXISTS idx_document_shares_user ON document_shares(user_id);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    user_id TEXT,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '新会话',
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    rating TEXT NOT NULL,
                    rating_value INTEGER NOT NULL DEFAULT 0,
                    kb_id TEXT,
                    document_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS retrieval_gaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kb_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    best_score REAL NOT NULL DEFAULT 0,
                    resolved INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS data_sources (
                    id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    interval_minutes INTEGER NOT NULL DEFAULT 60,
                    last_synced_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bm25_index (
                    chunk_id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    tokens_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

        # 兼容旧数据库：documents 表补充 access_mode 列。
        self._ensure_column("documents", "access_mode", "TEXT NOT NULL DEFAULT 'public'")
        self._ensure_column("knowledge_bases", "toc_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("knowledge_bases", "overview", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("feedback", "rating_value", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("feedback", "kb_id", "TEXT")
        self._ensure_column("feedback", "document_id", "TEXT")
        self._ensure_column("messages", "user_id", "TEXT")
        self._ensure_column("messages", "sources_json", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("sessions", "summary", "TEXT NOT NULL DEFAULT ''")

    # 知识库

    def create_kb(self, name: str, description: str = "") -> KnowledgeBase:
        kb_id = _new_id("kb")
        created_at = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO knowledge_bases(id, name, description, created_at) VALUES (?, ?, ?, ?)",
                (kb_id, name, description, created_at),
            )
        return KnowledgeBase(id=kb_id, name=name, description=description, created_at=created_at)

    def list_kbs(self) -> list[KnowledgeBase]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM knowledge_bases ORDER BY created_at DESC").fetchall()
        return [KnowledgeBase(**dict(row)) for row in rows]

    def get_kb(self, kb_id: str) -> KnowledgeBase | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
        return KnowledgeBase(**dict(row)) if row else None

    def delete_kb(self, kb_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))

    # 文档

    def set_kb_toc(self, kb_id: str, toc: list[dict[str, Any]], overview: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE knowledge_bases SET toc_json = ?, overview = ? WHERE id = ?",
                (json.dumps(toc, ensure_ascii=False), overview, kb_id),
            )

    def get_kb_toc(self, kb_id: str) -> tuple[list[dict[str, Any]], str]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT toc_json, overview FROM knowledge_bases WHERE id = ?", (kb_id,)
            ).fetchone()
        if not row:
            return [], ""
        try:
            toc = json.loads(row["toc_json"] or "{}")
            toc = toc if isinstance(toc, list) else []
        except json.JSONDecodeError:
            toc = []
        return toc, str(row["overview"] or "")

    def create_api_key(self, name: str, user_id: str, key_hash: str) -> dict[str, Any]:
        key_id = _new_id("key")
        created_at = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO api_keys(id, user_id, name, key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (key_id, user_id, name, key_hash, created_at),
            )
        return {"id": key_id, "name": name, "created_at": created_at}

    def list_api_keys(self, user_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id, name, revoked, last_used_at, created_at FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", (key_hash,)
            ).fetchone()
        return dict(row) if row else None

    def touch_api_key(self, key_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), key_id))

    def revoke_api_key(self, key_id: str, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ? AND user_id = ?", (key_id, user_id))

    def save_bm25_tokens(self, kb_id: str, chunk_tokens: dict[str, tuple[str, str, list[str]]]) -> None:
        updated_at = _now()
        with self.connection() as conn:
            for chunk_id, (document_id, text, tokens) in chunk_tokens.items():
                conn.execute(
                    "INSERT INTO bm25_index(chunk_id, kb_id, document_id, tokens_json, text, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(chunk_id) DO UPDATE SET kb_id=excluded.kb_id, document_id=excluded.document_id, tokens_json=excluded.tokens_json, text=excluded.text, updated_at=excluded.updated_at",
                    (chunk_id, kb_id, document_id, json.dumps(tokens, ensure_ascii=False), text, updated_at),
                )

    def get_bm25_tokens(self, kb_id: str, chunk_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
        sql = "SELECT chunk_id, document_id, tokens_json, text FROM bm25_index WHERE kb_id = ?"
        params: list[Any] = [kb_id]
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            sql += f" AND chunk_id IN ({placeholders})"
            params.extend(chunk_ids)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = {}
        for row in rows:
            try:
                tokens = json.loads(row["tokens_json"] or "[]")
            except json.JSONDecodeError:
                tokens = []
            result[row["chunk_id"]] = {"document_id": row["document_id"], "text": row["text"], "tokens": tokens}
        return result

    def delete_bm25_tokens(self, kb_id: str | None = None, document_id: str | None = None) -> None:
        sql = "DELETE FROM bm25_index WHERE 1=1"
        params: list[Any] = []
        if kb_id:
            sql += " AND kb_id = ?"; params.append(kb_id)
        if document_id:
            sql += " AND document_id = ?"; params.append(document_id)
        with self.connection() as conn:
            conn.execute(sql, params)

    def add_document(self, record: DocumentRecord) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO documents(id, kb_id, name, file_path, content_hash, status, access_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record.id, record.kb_id, record.name, record.file_path, record.content_hash, record.status, record.access_mode, record.created_at),
            )

    def create_source(self, kb_id: str, kind: str, name: str, config: dict[str, Any], interval_minutes: int = 60) -> dict[str, Any]:
        source_id = _new_id("src")
        created_at = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO data_sources(id, kb_id, kind, name, config_json, interval_minutes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_id, kb_id, kind, name, json.dumps(config, ensure_ascii=False), interval_minutes, created_at),
            )
        return {"id": source_id, "kb_id": kb_id, "kind": kind, "name": name, "config": config, "interval_minutes": interval_minutes, "enabled": 1, "created_at": created_at}

    def list_sources(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM data_sources"
        params: list[Any] = []
        if kb_id:
            sql += " WHERE kb_id = ?"
            params.append(kb_id)
        sql += " ORDER BY created_at DESC"
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["config"] = json.loads(item.pop("config_json") or "{}")
            except json.JSONDecodeError:
                item["config"] = {}
            result.append(item)
        return result

    def update_source(self, source_id: str, *, name: str | None = None, config: dict[str, Any] | None = None, enabled: bool | None = None, interval_minutes: int | None = None) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?"); params.append(name)
        if config is not None:
            sets.append("config_json = ?"); params.append(json.dumps(config, ensure_ascii=False))
        if enabled is not None:
            sets.append("enabled = ?"); params.append(1 if enabled else 0)
        if interval_minutes is not None:
            sets.append("interval_minutes = ?"); params.append(interval_minutes)
        if not sets:
            return
        params.append(source_id)
        with self.connection() as conn:
            conn.execute(f"UPDATE data_sources SET {', '.join(sets)} WHERE id = ?", params)

    def delete_source(self, source_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM data_sources WHERE id = ?", (source_id,))

    def mark_source_synced(self, source_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE data_sources SET last_synced_at = ? WHERE id = ?", (_now(), source_id))
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO documents(id, kb_id, name, file_path, content_hash, status, access_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record.id, record.kb_id, record.name, record.file_path, record.content_hash, record.status, record.access_mode, record.created_at),
            )

    def get_document_by_hash(self, kb_id: str, content_hash: str) -> DocumentRecord | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE kb_id = ? AND content_hash = ?",
                (kb_id, content_hash),
            ).fetchone()
        return DocumentRecord(**dict(row)) if row else None

    def list_documents(self, kb_id: str) -> list[DocumentRecord]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE kb_id = ? ORDER BY created_at DESC",
                (kb_id,),
            ).fetchall()
        return [DocumentRecord(**dict(row)) for row in rows]

    def get_document(self, document_id: str) -> DocumentRecord | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return DocumentRecord(**dict(row)) if row else None

    def delete_document(self, document_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def replace_chunks(self, document_id: str, chunks: list[Chunk]) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            conn.executemany(
                """
                INSERT INTO chunks(id, kb_id, document_id, document_name, text, chunk_index, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c.id,
                        c.kb_id,
                        c.document_id,
                        c.document_name,
                        c.text,
                        c.index,
                        json.dumps(c.tags, ensure_ascii=False),
                    )
                    for c in chunks
                ],
            )

    def list_chunks(self, kb_id: str, document_id: str | None = None) -> list[Chunk]:
        sql = "SELECT * FROM chunks WHERE kb_id = ?"
        params: list[Any] = [kb_id]
        if document_id:
            sql += " AND document_id = ?"
            params.append(document_id)
        sql += " ORDER BY document_id, chunk_index"
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            Chunk(
                id=row["id"],
                kb_id=row["kb_id"],
                document_id=row["document_id"],
                document_name=row["document_name"],
                text=row["text"],
                index=row["chunk_index"],
                tags=json.loads(row["tags_json"] or "[]"),
            )
            for row in rows
        ]

    def delete_chunks(self, kb_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM chunks WHERE kb_id = ?", (kb_id,))

    def list_chunks_by_documents(self, kb_id: str, document_ids: list[str]) -> list[Chunk]:
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        sql = f"SELECT * FROM chunks WHERE kb_id = ? AND document_id IN ({placeholders}) ORDER BY document_id, chunk_index"
        with self.connection() as conn:
            rows = conn.execute(sql, [kb_id, *document_ids]).fetchall()
        return [
            Chunk(
                id=row["id"],
                kb_id=row["kb_id"],
                document_id=row["document_id"],
                document_name=row["document_name"],
                text=row["text"],
                index=row["chunk_index"],
                tags=json.loads(row["tags_json"] or "[]"),
            )
            for row in rows
        ]

    def set_document_access_mode(self, document_id: str, access_mode: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE documents SET access_mode = ? WHERE id = ?", (access_mode, document_id))

    def grant_document_permission(self, user_id: str, document_id: str, role: Role) -> DocumentPermission:
        permission_id = _new_id("dperm")
        created_at = _now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO user_document_permissions(id, user_id, document_id, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, document_id) DO UPDATE SET role = excluded.role
                """,
                (permission_id, user_id, document_id, role.value, created_at),
            )
        return DocumentPermission(
            id=permission_id,
            user_id=user_id,
            document_id=document_id,
            role=role,
            created_at=created_at,
        )

    def revoke_document_permission(self, user_id: str, document_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM user_document_permissions WHERE user_id = ? AND document_id = ?", (user_id, document_id))

    def get_document_permissions(self, document_id: str) -> list[DocumentPermission]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM user_document_permissions WHERE document_id = ?", (document_id,)).fetchall()
        return [
            DocumentPermission(
                id=row["id"],
                user_id=row["user_id"],
                document_id=row["document_id"],
                role=Role(row["role"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_user_document_permission(self, user_id: str, document_id: str) -> DocumentPermission | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM user_document_permissions WHERE user_id = ? AND document_id = ?",
                (user_id, document_id),
            ).fetchone()
        if not row:
            return None
        return DocumentPermission(
            id=row["id"],
            user_id=row["user_id"],
            document_id=row["document_id"],
            role=Role(row["role"]),
            created_at=row["created_at"],
        )

    def list_user_document_ids(self, user_id: str) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute("SELECT document_id FROM user_document_permissions WHERE user_id = ?", (user_id,)).fetchall()
        return [row["document_id"] for row in rows]

    def share_document(self, document_id: str, user_id: str, created_by: str) -> DocumentShare:
        share_id = _new_id("share")
        created_at = _now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO document_shares(id, document_id, user_id, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(document_id, user_id) DO UPDATE SET created_by = excluded.created_by
                """,
                (share_id, document_id, user_id, created_by, created_at),
            )
        return DocumentShare(
            id=share_id,
            document_id=document_id,
            user_id=user_id,
            created_by=created_by,
            created_at=created_at,
        )

    def revoke_document_share(self, document_id: str, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM document_shares WHERE document_id = ? AND user_id = ?", (document_id, user_id))

    def list_document_shares(self, document_id: str) -> list[DocumentShare]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM document_shares WHERE document_id = ?", (document_id,)).fetchall()
        return [
            DocumentShare(
                id=row["id"],
                document_id=row["document_id"],
                user_id=row["user_id"],
                created_by=row["created_by"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_shared_document_ids(self, user_id: str) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute("SELECT document_id FROM document_shares WHERE user_id = ?", (user_id,)).fetchall()
        return [row["document_id"] for row in rows]

    # 用户与权限

    def create_user(self, username: str, password_hash: str, role: Role = Role.VIEWER, is_active: bool = True) -> User:
        user_id = _new_id("user")
        created_at = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, password_hash, role.value, int(is_active), created_at),
            )
        return User(
            id=user_id,
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            created_at=created_at,
        )

    def get_user_by_username(self, username: str) -> User | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row)

    def get_user(self, user_id: str) -> User | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row)

    def list_users(self) -> list[User]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
        return [self._row_to_user(row) for row in rows]

    def update_user(self, user_id: str, *, role: Role | None = None, is_active: bool | None = None, password_hash: str | None = None) -> None:
        fields: list[str] = []
        params: list[Any] = []
        if role is not None:
            fields.append("role = ?")
            params.append(role.value)
        if is_active is not None:
            fields.append("is_active = ?")
            params.append(int(is_active))
        if password_hash is not None:
            fields.append("password_hash = ?")
            params.append(password_hash)
        if not fields:
            return
        params.append(user_id)
        with self.connection() as conn:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)

    def delete_user(self, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def grant_kb_permission(self, user_id: str, kb_id: str, role: Role) -> KnowledgeBasePermission:
        permission_id = _new_id("perm")
        created_at = _now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO user_kb_permissions(id, user_id, kb_id, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, kb_id) DO UPDATE SET role = excluded.role
                """,
                (permission_id, user_id, kb_id, role.value, created_at),
            )
        return KnowledgeBasePermission(
            id=permission_id,
            user_id=user_id,
            kb_id=kb_id,
            role=role,
            created_at=created_at,
        )

    def revoke_kb_permission(self, user_id: str, kb_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM user_kb_permissions WHERE user_id = ? AND kb_id = ?", (user_id, kb_id))

    def get_kb_permissions(self, kb_id: str) -> list[KnowledgeBasePermission]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM user_kb_permissions WHERE kb_id = ?", (kb_id,)).fetchall()
        return [
            KnowledgeBasePermission(
                id=row["id"],
                user_id=row["user_id"],
                kb_id=row["kb_id"],
                role=Role(row["role"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_user_kb_permission(self, user_id: str, kb_id: str) -> KnowledgeBasePermission | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM user_kb_permissions WHERE user_id = ? AND kb_id = ?",
                (user_id, kb_id),
            ).fetchone()
        if not row:
            return None
        return KnowledgeBasePermission(
            id=row["id"],
            user_id=row["user_id"],
            kb_id=row["kb_id"],
            role=Role(row["role"]),
            created_at=row["created_at"],
        )

    def list_user_kb_ids(self, user_id: str) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute("SELECT kb_id FROM user_kb_permissions WHERE user_id = ?", (user_id,)).fetchall()
        return [row["kb_id"] for row in rows]

    @staticmethod
    def _row_to_user(row: sqlite3.Row | None) -> User | None:
        if not row:
            return None
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            role=Role(row["role"]),
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    # 对话与反馈

    def add_message(self, session_id: str, role: str, content: str, user_id: str | None = None, sources: list[dict[str, Any]] | None = None) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, user_id, sources_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, content, user_id, json.dumps(sources or [], ensure_ascii=False), _now()),
            )


    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row) if row else None

    def delete_message(self, message_id: int, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM messages WHERE id = ? AND user_id = ?", (message_id, user_id))
    def delete_session_messages(self, session_id: str, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ? AND user_id = ?", (session_id, user_id))


    def list_messages(self, session_id: str, user_id: str | None = None, limit: int = 12) -> list[dict[str, str]]:
        sql = "SELECT id, role, content, sources_json FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{"id": row["id"], "role": row["role"], "content": row["content"], "sources": json.loads(row["sources_json"] or "[]")} for row in reversed(rows)]

    def add_feedback(self, session_id: str, question: str, answer: str, rating: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO feedback(session_id, question, answer, rating, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, question, answer, rating, _now()),
            )



    def add_retrieval_gap(self, kb_id: str, user_id: str, question: str, best_score: float) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO retrieval_gaps(kb_id, user_id, question, best_score, created_at) VALUES (?, ?, ?, ?, ?)",
                (kb_id, user_id, question, best_score, _now()),
            )

    def list_retrieval_gaps(self, kb_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM retrieval_gaps"
        params: list[Any] = []
        if kb_id:
            sql += " WHERE kb_id = ?"
            params.append(kb_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def create_session(self, session_id: str, user_id: str, title: str = "新会话") -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO sessions(id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, user_id, title, _now(), _now()),
            )

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))


    def update_session_summary(self, session_id: str, user_id: str, summary: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE sessions SET summary = ? WHERE id = ? AND user_id = ?", (summary, session_id, user_id))
    def rename_session(self, session_id: str, user_id: str, title: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?", (title, _now(), session_id, user_id))
    def add_audit_log(self, user_id: str, action: str, resource_type: str, resource_id: str, detail: str = "") -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO audit_logs(user_id, action, resource_type, resource_id, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, action, resource_type, resource_id, detail, _now()),
            )
    def add_feedback_signal(
        self,
        session_id: str,
        question: str,
        answer: str,
        rating: str,
        rating_value: int,
        kb_id: str | None = None,
        document_id: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO feedback(session_id, question, answer, rating, rating_value, kb_id, document_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, question, answer, rating, rating_value, kb_id, document_id, _now()),
            )

    def list_feedback_signals(self, kb_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT document_id, rating_value FROM feedback WHERE kb_id = ? AND document_id IS NOT NULL",
                (kb_id,),
            ).fetchall()
        return [{"document_id": row["document_id"], "rating_value": row["rating_value"]} for row in rows]


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex}"





