"""完整偏好快照后台任务测试。"""

import asyncio

from core.memory.preference_worker import PreferenceExtractionWorker


class _Repository:
    def __init__(self, existing, messages):
        self.existing = list(existing)
        self.messages = list(messages)
        self.write = None

    async def list_users_with_pending_messages(self, _limit):
        return ["user-1"]

    async def get_pending_user_messages(self, _user_id, _limit):
        return self.messages, 10, self.messages[-1][0]

    async def list_preferences(self, _user_id):
        return list(self.existing)

    async def replace_all_and_advance(
        self,
        user_id,
        items,
        expected_last_message_id,
        last_message_id,
        *,
        source_text=None,
    ):
        self.write = (
            user_id,
            items,
            expected_last_message_id,
            last_message_id,
            source_text,
        )


class _Extractor:
    def __init__(self, result):
        self.result = result

    async def extract_full(self, _conversation_text, _existing):
        return self.result


def test_worker_overwrites_snapshot_and_advances_cursor() -> None:
    repository = _Repository(
        ["用户偏好苹果手机。"],
        [(11, "我现在更喜欢华为手机")],
    )
    worker = PreferenceExtractionWorker(
        repository, _Extractor(["用户偏好华为手机。"])
    )

    processed = asyncio.run(worker.run_once())

    assert processed == 1
    assert repository.write[:4] == (
        "user-1",
        ["用户偏好华为手机。"],
        10,
        11,
    )


def test_worker_rejects_unexplained_empty_snapshot() -> None:
    repository = _Repository(
        ["用户偏好苹果手机。"],
        [(12, "今天天气怎么样")],
    )
    worker = PreferenceExtractionWorker(repository, _Extractor([]))

    processed = asyncio.run(worker.run_once())

    assert processed == 0
    assert repository.write is None


def test_worker_allows_explicit_clear_all() -> None:
    repository = _Repository(
        ["用户偏好苹果手机。"],
        [(13, "请清空所有偏好")],
    )
    worker = PreferenceExtractionWorker(repository, _Extractor([]))

    processed = asyncio.run(worker.run_once())

    assert processed == 1
    assert repository.write[1:4] == ([], 10, 13)
