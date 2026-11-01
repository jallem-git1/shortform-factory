from pathlib import Path
from types import SimpleNamespace

import pytest

from shortform import pipeline
from shortform.config import Settings
from shortform.models import SceneRole, Script, Topic
from shortform.stages import llm, script
from shortform.stages.script import (
    Claim,
    DraftScene,
    FactCheck,
    Rewrite,
    ScriptDraft,
    ScriptError,
    SourceRef,
)

from .fakes import FakeClient

ONION = [
    (SceneRole.HOOK, "양파만 썰면 왜 눈물이 쏟아질까요?", "onion cutting close up"),
    (
        SceneRole.NOD,
        "맵기 때문이 아니에요. 양파가 방어용 가스를 뿜는 거예요.",
        "chef crying kitchen",
    ),
    (
        SceneRole.BODY,
        "양파 세포 속에는 황이 든 성분과 효소가 따로 보관돼 있어요.",
        "onion cells microscope",
    ),
    (
        SceneRole.BODY,
        "칼이 세포를 터뜨리면 둘이 섞여 눈을 찌르는 가스가 만들어져요.",
        "knife slicing onion",
    ),
    (
        SceneRole.BODY,
        "이 가스가 눈에 닿아 신경을 자극하면, 눈은 씻어내려고 눈물을 쏟아요.",
        "tear eye macro",
    ),
    (
        SceneRole.TWIST,
        "양파를 냉장고에 30분 두었다가 잘 드는 칼로 썰면 가스가 훨씬 덜 나와요.",
        "onion in fridge",
    ),
    (
        SceneRole.OUTRO,
        "눈물은 양파의 반격이었던 거죠. 오늘의 왜? 여기까지. 내일은 하늘이 왜 파란지 풀어볼게요.",
        "sliced onion on board",
    ),
]
SOURCE = SourceRef(url="https://www.britannica.com/science/onion", supports="양파 최루 성분")
TOPIC = Topic(title="양파 눈물", question="양파를 썰면 왜 눈물이 날까?")


def scenes(rows=ONION) -> list[DraftScene]:
    return [DraftScene(role=r, narration=n, caption="", image_prompt=p) for r, n, p in rows]


def draft(rows=ONION, **kw) -> ScriptDraft:
    base = dict(
        title="양파를 썰면 왜 눈물이 날까?",
        scenes=scenes(rows),
        sources=[SOURCE],
        confidence="established",
        hashtags=["과학", "#양파"],
    )
    return ScriptDraft(**(base | kw))


def ok_check(**kw) -> FactCheck:
    base = dict(
        claims=[
            Claim(
                claim="세포가 터지면 가스가 생긴다",
                verdict="supported",
                evidence_url=SOURCE.url,
                note="",
            )
        ],
        revised_scenes=[],
        extra_sources=[],
    )
    return FactCheck(**(base | kw))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(SHORTFORM_OUTPUT_DIR=tmp_path)


def test_happy_path_builds_script(settings: Settings) -> None:
    client = FakeClient(draft(), ok_check())
    result, report = script.generate(TOPIC, client=client, settings=settings)

    assert [s.role for s in result.scenes] == script.EXPECTED_ROLES
    assert [s.index for s in result.scenes] == list(range(7))
    assert result.title == "양파를 썰면 왜 눈물이 날까? #왜"
    assert result.hashtags[:2] == ["#왜", "#과학"]
    assert result.sources == [SOURCE.url]
    assert result.scenes[0].caption is None
    assert result.length_warnings() == []
    assert report.char_count == len(result.full_text)
    assert report.rewrites == 0 and not report.revised_by_fact_check

    # 초안·사실 확인은 웹 검색을 켜고, 설정한 모델로 불러요
    assert len(client.calls) == 2
    assert all(c["model"] == "claude-sonnet-5-5" for c in client.calls)
    assert all(c["tools"][0]["type"] == llm.WEB_SEARCH_TOOL for c in client.calls)
    assert client.calls[0]["fallbacks"] == "default"


def test_outro_uses_next_question(settings: Settings) -> None:
    client = FakeClient(draft(), ok_check())
    script.generate(TOPIC, client=client, settings=settings, next_question="하늘은 왜 파랄까?")
    assert "내일은 하늘은 왜 파랄까를 풀어볼게요" in client.calls[0]["messages"][0]["content"]


def test_broken_structure_is_regenerated_with_feedback(settings: Settings) -> None:
    broken = draft(rows=ONION[:-1])  # 마무리 빠짐
    client = FakeClient(broken, draft(), ok_check())
    result, _ = script.generate(TOPIC, client=client, settings=settings)
    assert len(result.scenes) == 7
    retry_prompt = client.calls[1]["messages"][0]["content"]
    assert "장면 구성이 달라요" in retry_prompt


def test_gives_up_after_two_bad_drafts(settings: Settings) -> None:
    no_source = draft(sources=[])
    client = FakeClient(no_source, no_source)
    with pytest.raises(ScriptError, match="근거 URL"):
        script.generate(TOPIC, client=client, settings=settings)


def test_fact_check_revision_is_applied(settings: Settings) -> None:
    fixed_rows = list(ONION)
    fixed_rows[5] = (
        SceneRole.TWIST,
        "양파를 차갑게 두었다가 잘 드는 칼로 썰면 가스가 덜 퍼진다는 실험이 있어요.",
        "onion fridge",
    )
    check = ok_check(
        claims=[
            Claim(
                claim="30분이면 된다", verdict="incorrect", evidence_url="", note="시간 근거 없음"
            )
        ],
        revised_scenes=scenes(fixed_rows),
        extra_sources=[SourceRef(url="https://example.ac.kr/onion", supports="냉장 효과")],
    )
    client = FakeClient(draft(), check)
    result, report = script.generate(TOPIC, client=client, settings=settings)
    assert "실험이 있어요" in result.scenes[5].narration
    assert report.revised_by_fact_check
    assert result.sources == [SOURCE.url, "https://example.ac.kr/onion"]


def test_incorrect_claim_without_fix_fails(settings: Settings) -> None:
    check = ok_check(claims=[Claim(claim="x", verdict="incorrect", evidence_url="", note="")])
    with pytest.raises(ScriptError, match="틀린 주장"):
        script.generate(TOPIC, client=FakeClient(draft(), check), settings=settings)


def test_unverified_claims_become_warnings(settings: Settings) -> None:
    check = ok_check(claims=[Claim(claim="30분", verdict="unverified", evidence_url="", note="")])
    _, report = script.generate(TOPIC, client=FakeClient(draft(), check), settings=settings)
    assert any("출처를 찾지 못한 주장" in w for w in report.warnings)


def test_too_short_is_rewritten(settings: Settings) -> None:
    short_rows = [
        (r, n.split(".")[0] + ("?" if r == SceneRole.HOOK else "요."), p) for r, n, p in ONION
    ]
    short_rows[-1] = (SceneRole.OUTRO, "오늘의 왜? 여기까지. 내일 또 봐요.", "onion")
    client = FakeClient(draft(rows=short_rows), ok_check(), Rewrite(scenes=scenes()))
    result, report = script.generate(TOPIC, client=client, settings=settings)
    assert report.rewrites == 1
    assert result.length_warnings() == []
    assert "공백 포함" in client.calls[2]["messages"][0]["content"]
    assert "tools" not in client.calls[2]  # 고쳐 쓰기에는 검색이 필요 없어요


def test_length_warning_kept_when_rewrites_fail(settings: Settings) -> None:
    long_rows = [(r, n + " " + n.replace("오늘의 왜? 여기까지. ", ""), p) for r, n, p in ONION]
    long = scenes(long_rows)
    client = FakeClient(
        draft(rows=long_rows), ok_check(), Rewrite(scenes=long), Rewrite(scenes=long)
    )
    _, report = script.generate(TOPIC, client=client, settings=settings)
    assert report.rewrites == script.MAX_REWRITES
    assert any("길어요" in w for w in report.warnings)


def test_refusal_raises(settings: Settings) -> None:
    refusal = SimpleNamespace(
        stop_reason="refusal",
        parsed_output=None,
        content=[],
        stop_details=SimpleNamespace(category="bio"),
    )
    with pytest.raises(llm.LLMError, match="거절"):
        script.generate(TOPIC, client=FakeClient(refusal), settings=settings)


def test_pause_turn_is_continued(settings: Settings) -> None:
    paused = SimpleNamespace(stop_reason="pause_turn", parsed_output=None, content=["searching"])
    client = FakeClient(paused, draft(), ok_check())
    script.generate(TOPIC, client=client, settings=settings)
    assert client.calls[1]["messages"][-1] == {"role": "assistant", "content": ["searching"]}


def test_style_warnings() -> None:
    rows = list(ONION)
    rows[0] = (SceneRole.HOOK, "부엌에서 양파만 썰기 시작하면 도대체 왜 눈물이 쏟아질까요?", "x")
    warnings = script.style_warnings(scenes(rows))
    assert any(w.startswith("훅이") for w in warnings)
    assert any("문장" in w for w in warnings)


def test_run_writes_report(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "make_client", lambda s: FakeClient(draft(), ok_check()))
    job = pipeline.Job.create("양파", settings)
    job.write("topic.json", TOPIC)
    stages = [s for s in pipeline.default_stages() if s.name == "script"]
    pipeline.run(job, stages)
    assert job.read("script.json", Script).scenes[0].role == SceneRole.HOOK
    assert job.read(script.REPORT_FILE, script.ScriptReport).claims


def test_missing_api_key_is_clear(settings: Settings) -> None:
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm.make_client(settings)
