from pathlib import Path

import pytest

from shortform import pipeline
from shortform.config import Settings
from shortform.models import (
    Scene,
    SceneRole,
    Script,
    StageStatus,
    Topic,
    UploadResult,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(SHORTFORM_OUTPUT_DIR=tmp_path)


def fake_topic(job: pipeline.Job) -> Topic:
    return Topic(title="하품", question=f"{job.state.topic_hint}?")


def fake_script(job: pipeline.Job) -> Script:
    topic = job.read("topic.json", Topic)
    return Script(
        title=topic.title,
        scenes=[Scene(index=0, role=SceneRole.HOOK, narration=topic.question, image_prompt="yawn")],
    )


def fake_upload(job: pipeline.Job) -> UploadResult:
    return UploadResult(video_id="abc", url="https://youtu.be/abc", privacy="private")


def broken(job: pipeline.Job) -> Topic:
    raise RuntimeError("API 키 없음")


STAGES = [
    pipeline.Stage("topic", "topic.json", Topic, fake_topic),
    pipeline.Stage("script", "script.json", Script, fake_script),
    pipeline.Stage("upload", "upload.json", UploadResult, fake_upload, needs_approval=True),
]


def test_stages_pass_data_through_files(settings: Settings) -> None:
    job = pipeline.Job.create("하품은 왜 옮을까", settings)
    pipeline.run(job, STAGES, stop="script")

    script = job.read("script.json", Script)
    assert script.scenes[0].narration == "하품은 왜 옮을까?"
    reloaded = pipeline.Job.load(job.state.job_id, settings)
    assert reloaded.state.stages == {"topic": StageStatus.DONE, "script": StageStatus.DONE}


def test_upload_waits_for_approval(settings: Settings) -> None:
    job = pipeline.Job.create("테스트", settings)
    with pytest.raises(pipeline.ApprovalRequired):
        pipeline.run(job, STAGES)
    assert not (job.dir / "upload.json").exists()

    job.state.approved = True
    pipeline.run(job, STAGES, start="upload")
    assert (job.dir / "upload.json").exists()


def test_failure_is_recorded(settings: Settings) -> None:
    job = pipeline.Job.create("테스트", settings)
    with pytest.raises(RuntimeError):
        pipeline.run(job, [pipeline.Stage("topic", "topic.json", Topic, broken)])
    state = pipeline.Job.load(job.state.job_id, settings).state
    assert state.stages["topic"] == StageStatus.FAILED
    assert "API 키 없음" in state.errors["topic"]


def test_default_stages_are_wired() -> None:
    names = [s.name for s in pipeline.default_stages()]
    assert names == ["topic", "script", "tts", "visuals", "render", "upload"]


def test_script_length_warning() -> None:
    short = Script(
        title="t",
        scenes=[Scene(index=0, role=SceneRole.HOOK, narration="짧아요", image_prompt="x")],
    )
    assert short.length_warnings()
