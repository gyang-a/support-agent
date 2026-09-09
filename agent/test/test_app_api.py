"""FastAPI 后端入口与请求模型测试。"""

import pytest
from pydantic import ValidationError

pytest.importorskip("fastapi")

from app.app_main import app
from app.schemas.chat import ChatRequest


def test_chat_route_is_registered() -> None:
    """清理包结构后 SSE 聊天路由仍可从项目根目录导入。"""
    # 新版 FastAPI 会延迟展开 include_router，OpenAPI 才是公开路由契约。
    paths = set(app.openapi()["paths"])

    assert "/api/chat" in paths


def test_chat_identity_fields_cannot_be_null() -> None:
    """空身份不能进入质量追踪和用户私有工具。"""
    with pytest.raises(ValidationError):
        ChatRequest(query="查询我的订单", user_id=None, session_id=None)
