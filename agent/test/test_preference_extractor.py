"""偏好提取器的模型接口兼容性测试。"""

import asyncio

from langchain_core.messages import AIMessage

from core.memory.preference_extractor import PreferenceExtractor


class _PlainChatModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def with_structured_output(self, _schema):
        raise AssertionError("不应调用 response_format 结构化输出")

    async def ainvoke(self, messages):
        self.calls += 1
        assert messages
        return AIMessage(content=self.content)


def test_extract_uses_plain_chat_and_parses_json_fence() -> None:
    """不支持 response_format 的模型也能返回并保存偏好。"""
    model = _PlainChatModel(
        '```json\n{"full_items":["用户偏好苹果品牌的手机。"]}\n```'
    )

    result = asyncio.run(
        PreferenceExtractor(model).extract(
            "user: 我喜欢苹果手机，请记住",
            existing=[],
        )
    )

    assert result == ["用户偏好苹果品牌的手机。"]
    assert model.calls == 1


def test_extract_returns_complete_snapshot_including_existing_items() -> None:
    model = _PlainChatModel(
        '{"full_items":["用户偏好苹果品牌的手机。","用户预算不超过6000元。"]}'
    )

    result = asyncio.run(
        PreferenceExtractor(model).extract_full(
            "user: 我的预算不超过6000元",
            existing=["用户偏好苹果品牌的手机。"],
        )
    )

    assert result == ["用户偏好苹果品牌的手机。", "用户预算不超过6000元。"]


def test_extract_rejects_non_json_response() -> None:
    """模型输出不符合约定时安全降级为空列表。"""
    model = _PlainChatModel("用户喜欢苹果手机。")

    result = asyncio.run(
        PreferenceExtractor(model).extract("user: 我喜欢苹果手机", existing=[])
    )

    assert result == []
