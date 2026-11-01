"""주제 발굴: job.state.topic_hint 를 '왜?' 질문 하나로 다듬습니다. (작업 B·D)

- 제외 주제(치료·투자·정치 등)면 멈춰요.
- 최근 90일 안에 만든 주제와 같은 현상이면 멈춰요 (output/ 의 다른 작업 폴더를 봐요).

읽는 파일: job.json
돌려주는 것: Topic
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from ..models import JobState, Topic
from ..pipeline import Job
from . import llm
from .prompts import TOPIC_SYSTEM

DUPLICATE_WINDOW_DAYS = 90


class TopicRejected(ValueError):
    """제외 주제이거나 최근 주제와 겹칠 때."""


class TopicDraft(BaseModel):
    title: str = Field(description="현상을 가리키는 짧은 이름")
    question: str = Field(description="'~는 왜 ~할까?' 꼴의 질문 한 문장")
    notes: str = Field(description="대본 작가에게 줄 메모")
    excluded_reason: str = Field(description="제외 주제면 이유, 아니면 빈 문자열")
    duplicate_of: str = Field(description="겹치는 최근 주제, 없으면 빈 문자열")


def recent_topics(
    output_dir: Path, *, exclude_job: str, days: int = DUPLICATE_WINDOW_DAYS
) -> list[str]:
    """최근 days 일 안에 만든 다른 작업들의 질문 목록."""
    since = datetime.now(UTC) - timedelta(days=days)
    found: list[str] = []
    if not output_dir.exists():
        return found
    for job_dir in sorted(output_dir.iterdir()):
        if job_dir.name == exclude_job:
            continue
        state_file, topic_file = job_dir / "job.json", job_dir / "topic.json"
        if not (state_file.is_file() and topic_file.is_file()):
            continue
        try:
            state = JobState.model_validate_json(state_file.read_text(encoding="utf-8"))
            topic = Topic.model_validate_json(topic_file.read_text(encoding="utf-8"))
        except ValueError:
            continue  # 깨진 폴더는 건너뜀
        if state.created_at >= since:
            found.append(topic.question)
    return found


def build_prompt(hint: str, recent: list[str]) -> str:
    recent_block = "\n".join(f"- {q}" for q in recent) if recent else "(없음)"
    return f"주제 힌트: {hint}\n\n최근 {DUPLICATE_WINDOW_DAYS}일 안에 만든 주제:\n{recent_block}"


def run(job: Job) -> Topic:
    settings = job.settings
    recent = recent_topics(settings.output_dir, exclude_job=job.state.job_id)
    draft = llm.ask(
        llm.make_client(settings),
        model=settings.topic_model,
        system=TOPIC_SYSTEM,
        prompt=build_prompt(job.state.topic_hint, recent),
        output=TopicDraft,
    )
    if draft.excluded_reason.strip():
        raise TopicRejected(f"제외 주제예요: {draft.excluded_reason}")
    if draft.duplicate_of.strip():
        raise TopicRejected(f"최근 주제와 겹쳐요: {draft.duplicate_of}")
    question = draft.question.strip()
    if "왜" not in question:
        raise TopicRejected(f"'왜?' 질문이 아니에요: {question}")
    if not question.endswith("?"):
        question += "?"
    return Topic(title=draft.title.strip(), question=question, notes=draft.notes.strip())
