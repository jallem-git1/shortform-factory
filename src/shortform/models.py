"""단계 사이에 주고받는 데이터 형식.

각 단계는 작업 폴더(output/<job_id>/)에 아래 모델을 JSON 파일로 저장하고,
다음 단계는 그 파일을 읽어서 이어 갑니다. 그래서 단계 하나만 다시 돌리거나
다른 구현으로 갈아 끼울 수 있습니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

# 포맷 가이드(작업 B) 기준: 45~55초, 원고 250~320자 (공백 포함, 장면 사이는 공백 한 칸)
TARGET_SECONDS = (45, 55)
TARGET_CHARS = (250, 320)


class SceneRole(StrEnum):
    """포맷 가이드의 구성: 훅 - 맞장구 - 본문 3단계 - 반전 - 마무리."""

    HOOK = "hook"
    NOD = "nod"
    BODY = "body"
    TWIST = "twist"
    OUTRO = "outro"


class Topic(BaseModel):
    """topic 단계 결과 (topic.json)."""

    title: str
    question: str = Field(description="영상이 답할 '왜?' 질문")
    notes: str = ""


class Scene(BaseModel):
    index: int
    role: SceneRole
    narration: str = Field(description="내레이션 문장, 해요체")
    caption: str | None = Field(None, description="화면 자막, 없으면 narration 사용")
    image_prompt: str = Field(description="이미지 검색어 또는 생성 프롬프트")


class Script(BaseModel):
    """script 단계 결과 (script.json)."""

    title: str
    scenes: list[Scene]
    sources: list[str] = Field(default_factory=list, description="사실 확인에 쓴 출처 URL")
    hashtags: list[str] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return " ".join(s.narration for s in self.scenes)

    def length_warnings(self) -> list[str]:
        n = len(self.full_text)
        lo, hi = TARGET_CHARS
        if n < lo:
            return [f"원고가 {n}자로 짧아요 (목표 {lo}~{hi}자)"]
        if n > hi:
            return [f"원고가 {n}자로 길어요 (목표 {lo}~{hi}자)"]
        return []


class AudioClip(BaseModel):
    scene_index: int
    path: Path
    duration: float = Field(description="초")
    word_timings: list[tuple[str, float, float]] = Field(
        default_factory=list, description="(단어, 시작초, 끝초) — 자막 싱크용"
    )


class Narration(BaseModel):
    """tts 단계 결과 (narration.json)."""

    clips: list[AudioClip]

    @property
    def total_seconds(self) -> float:
        return sum(c.duration for c in self.clips)


class Visual(BaseModel):
    scene_index: int
    path: Path
    source: str = Field(description="pexels | pixabay | ai 등")
    license_note: str = Field("", description="저작권 기록: 원본 URL, 작가, 라이선스")


class Visuals(BaseModel):
    """visuals 단계 결과 (visuals.json)."""

    items: list[Visual]


class RenderResult(BaseModel):
    """render 단계 결과 (render.json)."""

    video_path: Path
    duration: float
    width: int = 1080
    height: int = 1920


class UploadResult(BaseModel):
    """upload 단계 결과 (upload.json)."""

    video_id: str
    url: str
    privacy: str
    scheduled_at: datetime | None = None


class StageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class JobState(BaseModel):
    """작업 하나의 진행 상태 (job.json)."""

    job_id: str
    topic_hint: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stages: dict[str, StageStatus] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    approved: bool = Field(False, description="사람 검수 승인 여부, 승인 전에는 업로드 안 함")
