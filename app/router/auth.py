"""Minimal username/password registration and revocable cookie sessions."""
import asyncio
import time
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.service.auth import (
    COOKIE_NAME, LoginSession, User, current_user, new_user, public_user, token_hash, verify_password,
)
from app.service.workspace_store import sessions
from config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.lower()


async def issue_session(db, user, request, response):
    settings = get_settings()
    old_token = request.cookies.get(COOKIE_NAME)
    if old_token:
        await db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old_token)))
    now = int(time.time())
    await db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=token_hash(token), user_id=user.id,
                        expires_at=now + settings.auth_session_days * 86400))
    await db.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=settings.auth_session_days * 86400,
                        httponly=True, secure=settings.auth_cookie_secure, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return public_user(user)


@router.post("/register", status_code=201)
async def register(body: Credentials, request: Request, response: Response):
    user = await new_user(body.username, body.password)
    async with sessions()() as db:
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "用户名已被使用") from None
        return await issue_session(db, user, request, response)


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response):
    async with sessions()() as db:
        user = await db.scalar(select(User).where(User.username == body.username))
        # Run the same expensive password operation even for an unknown username.
        encoded = user.password_hash if user else "scrypt$" + "00" * 16 + "$" + "00" * 64
        valid = await asyncio.to_thread(verify_password, body.password, encoded)
        if not user or not valid:
            raise HTTPException(401, "用户名或密码错误")
        return await issue_session(db, user, request, response)


@router.get("/me")
async def me(response: Response, user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return public_user(user)


@router.post("/logout")
async def logout(request: Request, response: Response):
    async with sessions()() as db:
        token = request.cookies.get(COOKIE_NAME, "")
        await db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
        await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
