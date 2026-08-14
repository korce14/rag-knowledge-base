from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import AuthPrincipal, CurrentUser, get_current_user
from .config import settings
from .models import Role
from .pipeline import KnowledgeBaseService


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: Role = Role.VIEWER
    is_active: bool = True


class UpdateUserRequest(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class GrantPermissionRequest(BaseModel):
    user_id: str
    role: Role


class ShareDocumentRequest(BaseModel):
    user_id: str


service = KnowledgeBaseService()
app = FastAPI(title="RAG 知识库", version="2.0.0")
app.state.service = service
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", **service.capabilities()}


@app.post("/api/auth/login")
async def login(payload: LoginRequest) -> dict:
    user = service.login(payload.username, payload.password)
    from .security import create_access_token

    token = create_access_token(user.username, user.role.value, user.id)
    return {"token": token, "user": user.to_dict()}


@app.get("/api/auth/me")
async def me(principal: CurrentUser) -> dict:
    return principal.user.to_dict()


@app.get("/api/settings")
async def get_settings(principal: CurrentUser) -> dict:
    return service.capabilities()


@app.get("/api/prompts")
async def list_prompts(principal: CurrentUser) -> dict:
    return service.prompt_versions()


# 用户管理

@app.get("/api/users")
async def list_users(principal: CurrentUser) -> list[dict]:
    return [user.to_dict() for user in service.list_users(principal.user)]


@app.post("/api/users")
async def create_user(payload: CreateUserRequest, principal: CurrentUser) -> dict:
    user = service.create_user(
        principal.user,
        username=payload.username,
        password=payload.password,
        role=payload.role,
        is_active=payload.is_active,
    )
    return user.to_dict()


@app.patch("/api/users/{user_id}")
async def update_user(user_id: str, payload: UpdateUserRequest, principal: CurrentUser) -> dict:
    user = service.update_user(
        principal.user,
        user_id,
        role=payload.role,
        is_active=payload.is_active,
        password=payload.password,
    )
    return user.to_dict()


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: str, principal: CurrentUser) -> dict:
    service.delete_user(principal.user, user_id)
    return {"ok": True}


# 知识库与授权

@app.post("/api/knowledge_bases")
async def create_kb(payload: dict, principal: CurrentUser) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="知识库名称不能为空")
    kb = service.create_kb(principal.user, name, str(payload.get("description", "")))
    return kb.__dict__


@app.get("/api/knowledge_bases")
async def list_kbs(principal: CurrentUser) -> list[dict]:
    return [kb.__dict__ for kb in service.list_kbs(principal.user)]


@app.delete("/api/knowledge_bases/{kb_id}")
async def delete_kb(kb_id: str, principal: CurrentUser) -> dict:
    service.delete_kb(principal.user, kb_id)
    return {"ok": True}


@app.get("/api/knowledge_bases/{kb_id}/permissions")
async def list_kb_permissions(kb_id: str, principal: CurrentUser) -> list[dict]:
    return service.get_kb_permissions(principal.user, kb_id)


@app.post("/api/knowledge_bases/{kb_id}/permissions")
async def grant_kb_permission(kb_id: str, payload: GrantPermissionRequest, principal: CurrentUser) -> dict:
    service.grant_kb_permission(principal.user, kb_id, payload.user_id, payload.role)
    return {"ok": True}


@app.delete("/api/knowledge_bases/{kb_id}/permissions/{user_id}")
async def revoke_kb_permission(kb_id: str, user_id: str, principal: CurrentUser) -> dict:
    service.revoke_kb_permission(principal.user, kb_id, user_id)
    return {"ok": True}


# 文档

@app.post("/api/knowledge_bases/{kb_id}/documents")
async def upload_document(
    kb_id: str,
    principal: CurrentUser,
    file: Annotated[UploadFile, File()],
    tags: Annotated[str, Form()] = "",
    access_mode: Annotated[str, Form()] = "public",
) -> dict:
    content = await file.read()
    if len(content) > settings.guard_upload_max_bytes:
        raise HTTPException(status_code=413, detail="文件大小超过安全限制")

    upload_dir = settings.data_dir / "uploads" / kb_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "document.txt").suffix
    destination = upload_dir / f"{_short_id()}{suffix}"
    destination.write_bytes(content)

    try:
        result = service.ingest_path_with_mode(
            principal.user,
            kb_id,
            destination,
            tags.split(",") if tags else None,
            access_mode,
        )
    except FileExistsError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"文档解析失败：{exc}") from exc
    return result


@app.get("/api/knowledge_bases/{kb_id}/documents")
async def list_documents(kb_id: str, principal: CurrentUser) -> list[dict]:
    return service.list_visible_documents(principal.user, kb_id)


@app.delete("/api/documents/{document_id}")
async def remove_document(document_id: str, principal: CurrentUser) -> dict:
    record = service.db.get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="文档不存在")
    service.remove_document_visible(principal.user, document_id)
    return {"ok": True}


@app.get("/api/documents/{document_id}/permissions")
async def list_document_permissions(document_id: str, principal: CurrentUser) -> list[dict]:
    return service.get_document_permissions(principal.user, document_id)


@app.post("/api/documents/{document_id}/permissions")
async def grant_document_permission(document_id: str, payload: GrantPermissionRequest, principal: CurrentUser) -> dict:
    service.grant_document_permission(principal.user, document_id, payload.user_id, payload.role)
    return {"ok": True}


@app.delete("/api/documents/{document_id}/permissions/{user_id}")
async def revoke_document_permission(document_id: str, user_id: str, principal: CurrentUser) -> dict:
    service.revoke_document_permission(principal.user, document_id, user_id)
    return {"ok": True}


@app.patch("/api/documents/{document_id}/access-mode")
async def set_document_access_mode(document_id: str, payload: dict, principal: CurrentUser) -> dict:
    service.set_document_access_mode(principal.user, document_id, str(payload.get("access_mode", "")))
    return {"ok": True}


@app.get("/api/documents/{document_id}/shares")
async def list_document_shares(document_id: str, principal: CurrentUser) -> list[dict]:
    return service.list_document_shares(principal.user, document_id)


@app.post("/api/documents/{document_id}/shares")
async def share_document(document_id: str, payload: ShareDocumentRequest, principal: CurrentUser) -> dict:
    service.share_document(principal.user, document_id, payload.user_id)
    return {"ok": True}


@app.delete("/api/documents/{document_id}/shares/{user_id}")
async def revoke_document_share(document_id: str, user_id: str, principal: CurrentUser) -> dict:
    service.revoke_document_share(principal.user, document_id, user_id)
    return {"ok": True}


# 检索与问答

@app.post("/api/search")
async def search(payload: dict, principal: CurrentUser) -> dict:
    kb_id = str(payload.get("kb_id", "")).strip()
    query = str(payload.get("query", "")).strip()
    if not kb_id or not query:
        raise HTTPException(status_code=422, detail="kb_id 和 query 不能为空")
    hits = service.search_visible(
        principal.user,
        kb_id,
        query,
        top_k=int(payload.get("top_k", settings.top_k)),
        document_id=payload.get("document_id"),
        tags=payload.get("tags"),
    )
    return {
        "query": query,
        "results": [
            {
                "id": hit.chunk.id,
                "document_name": hit.chunk.document_name,
                "chunk_index": hit.chunk.index,
                "score": round(hit.score, 4),
                "text": hit.chunk.text,
            }
            for hit in hits
        ],
    }


@app.post("/api/chat")
async def chat(payload: dict, principal: CurrentUser) -> dict:
    kb_id = str(payload.get("kb_id", "")).strip()
    question = str(payload.get("question", "")).strip()
    if not kb_id or not question:
        raise HTTPException(status_code=422, detail="kb_id 和 question 不能为空")
    result = service.query_pipeline(
        principal.user,
        kb_id,
        question,
        session_id=str(payload.get("session_id", "default")),
        top_k=int(payload.get("top_k", settings.top_k)),
        document_id=payload.get("document_id"),
        tags=payload.get("tags"),
    )
    return result


@app.post("/api/chat/stream")
async def chat_stream(payload: dict, principal: CurrentUser) -> StreamingResponse:
    kb_id = str(payload.get("kb_id", "")).strip()
    question = str(payload.get("question", "")).strip()
    session_id = str(payload.get("session_id", "default"))

    async def event_stream():
        async for event in service.stream(
            principal.user,
            kb_id,
            question,
            session_id=session_id,
            top_k=int(payload.get("top_k", settings.top_k)),
            document_id=payload.get("document_id"),
            tags=payload.get("tags"),
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\\n\\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/feedback")
async def feedback(payload: dict, principal: CurrentUser) -> dict:
    service.feedback(
        str(payload.get("session_id", "default")),
        str(payload.get("question", "")),
        str(payload.get("answer", "")),
        str(payload.get("rating", "")),
        kb_id=payload.get("kb_id"),
        document_ids=payload.get("document_ids"),
    )

def _short_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


static_dir = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")





