"""Real SQL/session/cookie API tests, without external databases or model calls."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.app_main import app
from app.service import chat_service, workspace_stream
from app.service.auth import COOKIE_NAME, LoginSession, User, verify_password
from app.service.workspace_store import WorkspaceConversation, WorkspaceRun


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "auth.db").as_posix())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_):
        async with engine.begin() as connection:
            for model in (User, LoginSession, WorkspaceConversation, WorkspaceRun):
                await connection.run_sync(model.__table__.create)
        monkeypatch.setattr(chat_service, "database", SimpleNamespace(available=True, sessions=factory))
        yield
        await engine.dispose()

    monkeypatch.setattr(app.router, "lifespan_context", lifespan)
    with TestClient(app, headers={"X-Requested-With": "SupportAgent"}) as client:
        yield client


def register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": "demo-password"})
    assert response.status_code == 201, response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    return response.json()


def test_accounts_own_conversations_runs_and_cancel_permissions(client, monkeypatch):
    assert client.get("/api/workspace/conversations").status_code == 401
    assert client.post("/api/chat", json={"query": "hello"}).status_code == 401
    first = register(client, "alice")
    conversation = client.post("/api/workspace/conversations", json={"title": "Alice private"}).json()
    run_id = str(uuid.uuid4())
    called = []

    async def chat(query, user_id, session_id, *, account_id):
        called.append((user_id, account_id, session_id))
        yield 'data: {"content":"演示订单"}\n\n'
        yield 'data: {"done":true}\n\n'

    monkeypatch.setattr(workspace_stream, "stream_chat", chat)
    response = client.post("/api/workspace/runs", json={"id": run_id, "conversation_id": conversation["id"], "query": "我的订单"})
    assert response.status_code == 200 and "run.completed" in response.text
    assert called == [("user_1001", first["id"], conversation["id"])]
    assert len(client.get(f"/api/workspace/conversations/{conversation['id']}/runs").json()) == 1

    second = register(client, "bob")
    assert first["id"] != second["id"]
    assert client.get("/api/workspace/conversations").json() == []
    path = f"/api/workspace/conversations/{conversation['id']}"
    assert client.get(path + "/runs").status_code == 404
    assert client.patch(path, json={"title": "stolen"}).status_code == 404
    assert client.post(f"/api/workspace/runs/{run_id}/cancel").status_code == 404
    assert client.post("/api/workspace/runs", json={"id": str(uuid.uuid4()), "conversation_id": conversation["id"], "query": "hello"}).status_code == 404
    assert client.post("/api/auth/login", json={"username": "ALICE", "password": "demo-password"}).status_code == 200
    assert client.get("/api/workspace/conversations").json()[0]["title"] == "Alice private"


def test_registration_passwords_csrf_expiry_and_logout(client):
    register(client, "alice")
    assert client.post("/api/auth/register", json={"username": "ALICE", "password": "demo-password"}).status_code == 409
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/register", json={"username": "bad user", "password": "short"}).status_code == 422
    assert client.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-Requested-With": ""}).status_code == 403
    assert client.get("/api/auth/me").status_code == 200

    async def inspect_and_expire():
        async with chat_service.database.sessions() as db:
            user = await db.scalar(select(User))
            assert user.password_hash != "demo-password"
            assert verify_password("demo-password", user.password_hash)
            login = await db.scalar(select(LoginSession))
            assert login.token_hash != client.cookies.get(COOKIE_NAME)
            await db.execute(update(LoginSession).values(expires_at=int(time.time()) - 1))
            await db.commit()
    client.portal.call(inspect_and_expire)
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "demo-password"}).status_code == 200
    old_cookie = client.cookies.get(COOKIE_NAME)
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.set(COOKIE_NAME, old_cookie)
    assert client.get("/api/auth/me").status_code == 401


def test_account_scopes_checkpoint_audit_preferences_but_not_business_identity(monkeypatch):
    from langchain_core.messages import AIMessage
    from core.persistence import thread_id_for
    calls, records, preferences = [], [], []

    class Graph:
        async def ainvoke(self, state, config):
            calls.append((state, config))
            return {"messages": [AIMessage(content="done")]}

    async def preference(account_id):
        preferences.append(account_id)
        return "preferences for " + account_id

    async def record(*args):
        records.append(args)

    async def save_preferences(*args):
        pass

    monkeypatch.setattr(chat_service, "graph", Graph())
    monkeypatch.setattr(chat_service, "answer_cache", None)
    monkeypatch.setattr(chat_service, "_preference_context", preference)
    monkeypatch.setattr(chat_service, "_record_turn", record)
    monkeypatch.setattr(chat_service, "_save_explicit_preferences", save_preferences)

    async def run():
        for account in ("account_a", "account_b"):
            _ = [frame async for frame in chat_service.stream_chat("hello", "user_1001", "same-session", account_id=account)]
    asyncio.run(run())
    assert [state["user_id"] for state, _ in calls] == ["user_1001", "user_1001"]
    assert [config["configurable"]["thread_id"] for _, config in calls] == [thread_id_for(a, "same-session") for a in ("account_a", "account_b")]
    assert preferences == ["account_a", "account_b"]
    assert [record[0] for record in records] == preferences
