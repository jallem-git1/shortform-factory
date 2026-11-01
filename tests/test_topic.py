import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shortform import pipeline
from shortform.config import Settings
from shortform.models import Topic
from shortform.stages import llm, topic
from shortform.stages.topic import TopicDraft, TopicRejected

from .fakes import FakeClient


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(SHORTFORM_OUTPUT_DIR=tmp_path)


def reply(**kw) -> TopicDraft:
    base = dict(
        title="하품",
        question="하품은 왜 옮을까",
        notes="공감 가설",
        excluded_reason="",
        duplicate_of="",
    )
    return TopicDraft(**(base | kw))


def use(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> FakeClient:
    monkeypatch.setattr(llm, "make_client", lambda s: client)
    return client


def test_hint_becomes_why_question(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    client = use(monkeypatch, FakeClient(reply()))
    job = pipeline.Job.create("하품 전염", settings)
    result = topic.run(job)
    assert result == Topic(title="하품", question="하품은 왜 옮을까?", notes="공감 가설")
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in call  # Haiku 는 서버 대체 모델 미지원
    assert "하품 전염" in call["messages"][0]["content"]


def test_excluded_topic_is_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeClient(reply(excluded_reason="다이어트 효과")))
    with pytest.raises(TopicRejected, match="제외"):
        topic.run(pipeline.Job.create("살 빠지는 차", settings))


def test_recent_topics_are_sent_and_duplicates_rejected(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = pipeline.Job.create("예전", settings)
    old.write("topic.json", Topic(title="하품", question="하품은 왜 옮을까?"))
    ancient = pipeline.Job.create("아주 예전", settings)
    ancient.state.created_at = datetime.now(UTC) - timedelta(days=100)
    ancient.save_state()
    ancient.write("topic.json", Topic(title="딸꾹질", question="딸꾹질은 왜 할까?"))

    client = use(monkeypatch, FakeClient(reply(duplicate_of="하품은 왜 옮을까?")))
    job = pipeline.Job.create("하품이 옮는 이유", settings)
    with pytest.raises(TopicRejected, match="겹쳐요"):
        topic.run(job)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "하품은 왜 옮을까?" in prompt
    assert "딸꾹질" not in prompt  # 90일 지난 주제는 다시 만들어도 돼요


def test_recent_topics_skips_broken_folders(settings: Settings) -> None:
    broken = settings.output_dir / "broken"
    broken.mkdir(parents=True)
    (broken / "job.json").write_text("{", encoding="utf-8")
    (broken / "topic.json").write_text(json.dumps({"title": "x"}), encoding="utf-8")
    assert topic.recent_topics(settings.output_dir, exclude_job="none") == []


def test_non_why_question_is_rejected(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeClient(reply(question="하품에 대해 알아보자")))
    with pytest.raises(TopicRejected, match="왜"):
        topic.run(pipeline.Job.create("하품", settings))
