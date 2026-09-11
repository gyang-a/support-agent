"""MySQL-backed login sessions, separate from the fixed commerce demo identity."""
import asyncio
import hashlib
import hmac
import secrets
import time
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.service.workspace_store import sessions
from core.persistence.models import Base

COOKIE_NAME = "support_session"


class User(Base):
    __tablename__ = "auth_users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[int] = mapped_column(BigInteger)


class LoginSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), index=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, _ = encoded.split("$")
        return algorithm == "scrypt" and hmac.compare_digest(hash_password(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(user: User) -> dict:
    return {"id": user.id, "username": user.username}


async def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "请先登录")
    async with sessions()() as db:
        login = await db.get(LoginSession, token_hash(token))
        if login is None or login.expires_at <= int(time.time()):
            raise HTTPException(401, "登录已过期，请重新登录")
        user = await db.get(User, login.user_id)
        if user is None:
            raise HTTPException(401, "请重新登录")
        return user


async def new_user(username: str, password: str) -> User:
    return User(id="account_" + uuid.uuid4().hex, username=username,
                password_hash=await asyncio.to_thread(hash_password, password), created_at=int(time.time()))
