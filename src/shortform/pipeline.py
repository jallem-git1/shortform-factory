"""단계를 순서대로 실행하고 결과를 작업 폴더에 저장합니다."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .config import Settings
from .models import (
    JobState,
    Narration,
    RenderResult,
    Script,
    StageStatus,
    Topic,
    UploadResult,
    Visuals,
)

M = TypeVar("M", bound=BaseModel)


class Job:
    """output/<job_id>/ 폴더 하나 = 영상 한 편."""

    def __init__(self, root: Path, state: JobState, settings: Settings):
        self.root = root
        self.state = state
        self.settings = settings

    @property
    def dir(self) -> Path:
        return self.root / self.state.job_id

    @classmethod
    def create(cls, topic_hint: str, settings: Settings) -> Job:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", topic_hint).strip("-")[:30] or "job"
        state = JobState(job_id=f"{stamp}-{slug}", topic_hint=topic_hint)
        job = cls(settings.output_dir, state, settings)
        job.dir.mkdir(parents=True, exist_ok=False)
        job.save_state()
        return job

    @classmethod
    def load(cls, job_id: str, settings: Settings) -> Job:
        path = settings.output_dir / job_id / "job.json"
        state = JobState.model_validate_json(path.read_text(encoding="utf-8"))
        return cls(settings.output_dir, state, settings)

    def save_state(self) -> None:
        self.write("job.json", self.state)

    def write(self, filename: str, model: BaseModel) -> Path:
        path = self.dir / filename
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        return path

    def read(self, filename: str, model: type[M]) -> M:
        return model.model_validate_json((self.dir / filename).read_text(encoding="utf-8"))

    def subdir(self, name: str) -> Path:
        path = self.dir / name
        path.mkdir(exist_ok=True)
        return path


StageFunc = Callable[[Job], BaseModel]


@dataclass(frozen=True)
class Stage:
    name: str
    output_file: str
    model: type[BaseModel]
    func: StageFunc
    needs_approval: bool = False


class ApprovalRequired(Exception):
    """사람 검수 승인 전에 업로드 단계에 도달했을 때."""


def default_stages() -> list[Stage]:
    from .stages import render, script, topic, tts, upload, visuals

    return [
        Stage("topic", "topic.json", Topic, topic.run),
        Stage("script", "script.json", Script, script.run),
        Stage("tts", "narration.json", Narration, tts.run),
        Stage("visuals", "visuals.json", Visuals, visuals.run),
        Stage("render", "render.json", RenderResult, render.run),
        Stage("upload", "upload.json", UploadResult, upload.run, needs_approval=True),
    ]


def run(job: Job, stages: list[Stage], start: str | None = None, stop: str | None = None) -> None:
    """start 단계부터 stop 단계까지(포함) 실행합니다. 실패하면 상태를 남기고 다시 던집니다."""
    names = [s.name for s in stages]
    i = names.index(start) if start else 0
    j = names.index(stop) + 1 if stop else len(stages)

    for stage in stages[i:j]:
        if stage.needs_approval and not job.state.approved:
            raise ApprovalRequired(f"'{stage.name}' 단계는 검수 승인 후에 실행돼요")
        try:
            result = stage.func(job)
            if not isinstance(result, stage.model):
                raise TypeError(f"{stage.name} 단계가 {stage.model.__name__} 대신 {type(result)}")
            job.write(stage.output_file, result)
            job.state.stages[stage.name] = StageStatus.DONE
            job.state.errors.pop(stage.name, None)
        except Exception as e:
            job.state.stages[stage.name] = StageStatus.FAILED
            job.state.errors[stage.name] = f"{type(e).__name__}: {e}"
            raise
        finally:
            job.save_state()
