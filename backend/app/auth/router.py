"""认证路由：注册 / 登录 / 当前用户 / 改密码。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..db import User, UserRole, get_session
from .deps import get_current_user
from .security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- Schemas ----------
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_\-]+$")
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    use_default_model: bool
    created_at: float
    last_login: float | None


def _user_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "email": u.email,
        "role": u.role, "is_active": u.is_active,
        "use_default_model": u.use_default_model,
        "created_at": u.created_at, "last_login": u.last_login,
    }


# ---------- 路由 ----------
@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    s = get_settings()
    if not s.allow_register:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "公开注册已关闭，请联系管理员")

    exists = (await session.execute(
        select(User).where((User.username == body.username) | (User.email == body.email))
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名或邮箱已被使用")

    u = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=UserRole.USER.value,
        is_active=True,
        last_login=time.time(),
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    token = create_access_token(str(u.id), extra={"role": u.role, "username": u.username})
    return TokenResponse(access_token=token, user=_user_dict(u))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    u = (await session.execute(
        select(User).where(User.username == body.username)
    )).scalar_one_or_none()
    if not u or not verify_password(body.password, u.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not u.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    u.last_login = time.time()
    await session.commit()
    token = create_access_token(str(u.id), extra={"role": u.role, "username": u.username})
    return TokenResponse(access_token=token, user=_user_dict(u))


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(**_user_dict(user))


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原密码错误")
    user.hashed_password = hash_password(body.new_password)
    await session.commit()
    return {"ok": True}
