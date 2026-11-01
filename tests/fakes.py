"""Claude API 대신 쓰는 가짜 클라이언트. 미리 넣어 둔 답을 순서대로 돌려줘요."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel


class FakeClient:
    def __init__(self, *replies: BaseModel | SimpleNamespace):
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if not self.replies:
            raise AssertionError("예상보다 API 를 많이 불렀어요")
        reply = self.replies.pop(0)
        if isinstance(reply, SimpleNamespace):
            return reply
        expected = kwargs["output_format"]
        assert isinstance(reply, expected), f"{expected.__name__} 를 요청했는데 {type(reply)}"
        return SimpleNamespace(stop_reason="end_turn", parsed_output=reply, content=[])
