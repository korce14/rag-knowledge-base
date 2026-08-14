from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from .config import settings
from .models import Role, User
from .security import decode_access_token


@dataclass
class AuthPrincipal:
    user: User
    token_type: str = "user"


ROLE_LEVEL = {
    Role.VIEWER: 1,
    Role.EDITOR: 2,
    Role.ADMIN: 3,
}


def _service_token_user() -> User:
    return User(
        id="service_token",
        username="service",
        password_hash="",
        role=Role.ADMIN,
        is_active=True,
        created_at="",
    )


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthPrincipal:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌格式错误")

    if settings.auth_token and token == settings.auth_token:
        return AuthPrincipal(user=_service_token_user(), token_type="service")

    try:
        payload = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效或已过期") from exc

    db = request.app.state.service.db
    user = db.get_user(payload.get("uid", ""))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已被禁用")
    return AuthPrincipal(user=user, token_type="user")


async def require_role(
    principal: Annotated[AuthPrincipal, Depends(get_current_user)],
    minimum: Role,
) -> AuthPrincipal:
    if ROLE_LEVEL.get(principal.user.role, 0) < ROLE_LEVEL[minimum]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前用户没有此操作权限")
    return principal


CurrentUser = Annotated[AuthPrincipal, Depends(get_current_user)]
