"""Claude API 호출을 한곳에 모은 얇은 도우미. (작업 D)

topic·script 단계가 같이 씁니다. 응답은 항상 pydantic 모델로 받습니다(구조화 출력).
테스트에서는 make_client 를 가짜 클라이언트로 바꿔 끼워 API 를 부르지 않습니다.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from ..config import Settings

T = TypeVar("T", bound=BaseModel)

WEB_SEARCH_TOOL = "web_search_20260209"

# 안전 분류기가 거절했을 때 서버가 알아서 다른 모델로 다시 돌려 주는 기능.
# Claude API 에서 이 모델들만 지원해요 (Haiku 는 미지원).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

MAX_CONTINUES = 4  # 웹 검색이 길어져 pause_turn 으로 끊길 때 이어 부르는 최대 횟수


class LLMError(RuntimeError):
    """API 가 쓸 수 있는 답을 주지 않았을 때 (거절, 잘림, 형식 오류)."""


def make_client(settings: Settings) -> Any:
    """anthropic 클라이언트를 만듭니다. 키는 .env 의 ANTHROPIC_API_KEY 에서만 읽어요."""
    import anthropic

    if settings.anthropic_api_key is None:
        raise LLMError("ANTHROPIC_API_KEY 가 없어요. .env 에 넣어 주세요")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())


def ask(
    client: Any,
    *,
    model: str,
    system: str,
    prompt: str,
    output: type[T],
    web_search: bool = False,
    max_searches: int = 5,
    max_tokens: int = 16000,
) -> T:
    """프롬프트 하나를 보내고 output 모델로 검증된 답을 돌려줍니다."""
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "output_format": output,
    }
    betas: list[str] = []
    if model in FALLBACK_MODELS:
        betas.append(FALLBACK_BETA)
        kwargs["fallbacks"] = "default"
    if betas:
        kwargs["betas"] = betas
    if web_search:
        kwargs["tools"] = [
            {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}
        ]

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    for _ in range(MAX_CONTINUES + 1):
        response = client.beta.messages.parse(messages=messages, **kwargs)
        if response.stop_reason != "pause_turn":
            break
        # 서버 도구(웹 검색)가 오래 걸려 중간에 멈춘 경우: 받은 내용을 그대로 붙여 이어 갑니다.
        messages.append({"role": "assistant", "content": response.content})
    else:
        raise LLMError("웹 검색이 너무 오래 이어져서 멈췄어요")

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        raise LLMError(f"모델이 요청을 거절했어요 (분류: {category})")
    if response.stop_reason == "max_tokens":
        raise LLMError("답이 max_tokens 에서 잘렸어요")
    parsed = getattr(response, "parsed_output", None)
    if parsed is None:
        raise LLMError("구조화된 답(JSON)을 받지 못했어요")
    return parsed
