from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """SSE 聊天请求；身份字段不得为空。"""

    query: str = Field(min_length=1, max_length=4000)
    user_id: str = Field(default="user_1001", min_length=1, max_length=128)
    session_id: str = Field(default="default_session", min_length=1, max_length=128)
