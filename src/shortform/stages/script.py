"""대본 생성: Claude API 로 장면별 내레이션과 이미지 프롬프트를 만들고 출처를 남깁니다. (작업 D)

순서
1. 초안: 웹 검색을 켜고 포맷 가이드대로 장면 7개(훅-맞장구-본문3-반전-마무리)를 씁니다.
   구성 규칙을 어기면 이유를 알려 주고 한 번 다시 씁니다.
2. 사실 확인: 별도 호출이 웹 검색으로 주장을 하나씩 검증하고, 틀린 곳이 있으면 고친 장면을 줍니다.
   틀린 주장이 있는데 고친 본이 없거나 형식이 깨졌으면 실패로 멈춥니다.
3. 길이 맞추기: 250~320자를 벗어나면 최대 2번 고쳐 쓰고, 그래도 벗어나면 경고만 남깁니다.

검수자가 볼 수 있도록 사실 확인 결과와 경고는 script_report.json 에 따로 남깁니다.

읽는 파일: topic.json
돌려주는 것: Script
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import Settings
from ..models import TARGET_CHARS, Scene, SceneRole, Script, Topic
from ..pipeline import Job
from . import llm
from .prompts import (
    FACTCHECK_SYSTEM,
    OUTRO_DEFAULT_NEXT,
    OUTRO_FIXED,
    REWRITE_SYSTEM,
    SCRIPT_SYSTEM,
)

EXPECTED_ROLES = [
    SceneRole.HOOK,
    SceneRole.NOD,
    SceneRole.BODY,
    SceneRole.BODY,
    SceneRole.BODY,
    SceneRole.TWIST,
    SceneRole.OUTRO,
]
HOOK_MAX_CHARS = 25
SENTENCE_MAX_CHARS = 40
MAX_REWRITES = 2
REPORT_FILE = "script_report.json"


class ScriptError(RuntimeError):
    """규칙을 지킨 대본을 만들지 못했을 때."""


# ---- Claude 가 채우는 형식 -------------------------------------------------------


class DraftScene(BaseModel):
    role: SceneRole
    narration: str = Field(description="내레이션, 해요체")
    caption: str = Field(description="화면 자막을 따로 줄일 때만, 아니면 빈 문자열")
    image_prompt: str = Field(description="스톡 사진 검색용 영어 검색어 3~6단어")


class SourceRef(BaseModel):
    url: str
    supports: str = Field(description="이 링크가 뒷받침하는 내용 한 줄")


class ScriptDraft(BaseModel):
    title: str
    scenes: list[DraftScene]
    sources: list[SourceRef]
    confidence: Literal["established", "leading_hypothesis"]
    hashtags: list[str]


class Claim(BaseModel):
    claim: str
    verdict: Literal["supported", "incorrect", "unverified"]
    evidence_url: str = Field(description="판단 근거 URL, 없으면 빈 문자열")
    note: str = Field(description="판단 이유 한 줄")


class FactCheck(BaseModel):
    claims: list[Claim]
    revised_scenes: list[DraftScene] = Field(description="고칠 게 없으면 빈 목록")
    extra_sources: list[SourceRef]


class Rewrite(BaseModel):
    scenes: list[DraftScene]


# ---- 검수용 기록 (script_report.json) --------------------------------------------


class ScriptReport(BaseModel):
    model: str
    confidence: str
    char_count: int
    claims: list[Claim] = Field(default_factory=list)
    revised_by_fact_check: bool = False
    rewrites: int = 0
    warnings: list[str] = Field(default_factory=list)


# ---- 규칙 검사 -------------------------------------------------------------------


def char_count(scenes: list[DraftScene]) -> int:
    """models.Script.length_warnings 와 같은 기준 (공백 포함)."""
    return len(" ".join(s.narration.strip() for s in scenes))


def structure_errors(scenes: list[DraftScene], sources: list[SourceRef] | None = None) -> list[str]:
    """어기면 다시 써야 하는 규칙."""
    errors: list[str] = []
    roles = [s.role for s in scenes]
    if roles != EXPECTED_ROLES:
        want = ", ".join(r.value for r in EXPECTED_ROLES)
        got = ", ".join(r.value for r in roles)
        errors.append(f"장면 구성이 달라요. 필요: [{want}], 받은 것: [{got}]")
    if any(not s.narration.strip() for s in scenes):
        errors.append("내레이션이 빈 장면이 있어요")
    if any(not s.image_prompt.strip() for s in scenes):
        errors.append("image_prompt 가 빈 장면이 있어요")
    if scenes and scenes[0].role == SceneRole.HOOK:
        hook = scenes[0].narration
        if "?" not in hook and "반대" not in hook:
            errors.append("훅이 질문형이 아니에요")
    if scenes and scenes[-1].role == SceneRole.OUTRO and OUTRO_FIXED not in scenes[-1].narration:
        errors.append(f"마무리에 고정 멘트 '{OUTRO_FIXED}'가 없어요")
    if sources is not None and not any(s.url.startswith("http") for s in sources):
        errors.append("근거 URL 이 하나도 없어요")
    return errors


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+", text.strip()) if s]


def style_warnings(scenes: list[DraftScene]) -> list[str]:
    """지키면 좋지만 사람이 검수에서 판단할 규칙."""
    warnings: list[str] = []
    if scenes and len(scenes[0].narration.strip()) > HOOK_MAX_CHARS:
        warnings.append(
            f"훅이 {len(scenes[0].narration.strip())}자예요 (권장 {HOOK_MAX_CHARS}자 이내)"
        )
    for scene in scenes:
        for sentence in split_sentences(scene.narration):
            if len(sentence) > SENTENCE_MAX_CHARS:
                warnings.append(
                    f"{len(sentence)}자 문장 (권장 {SENTENCE_MAX_CHARS}자 이내): {sentence}"
                )
    return warnings


# ---- 프롬프트 --------------------------------------------------------------------


def outro_instruction(next_question: str | None) -> str:
    if next_question:
        return f"마무리 멘트: '{OUTRO_FIXED} 내일은 {next_question.rstrip('?')}를 풀어볼게요.'"
    return f"다음 편이 아직 없어요. 마무리는 '{OUTRO_FIXED} {OUTRO_DEFAULT_NEXT}'로 끝내세요."


def draft_prompt(topic: Topic, next_question: str | None, feedback: list[str]) -> str:
    lines = [
        f"질문: {topic.question}",
        f"주제: {topic.title}",
        f"기획 메모: {topic.notes or '(없음)'}",
        outro_instruction(next_question),
    ]
    if feedback:
        lines.append("지난 초안이 아래 규칙을 어겼어요. 고쳐서 다시 써 주세요:")
        lines += [f"- {e}" for e in feedback]
    return "\n".join(lines)


def scenes_json(scenes: list[DraftScene]) -> str:
    return json.dumps([s.model_dump(mode="json") for s in scenes], ensure_ascii=False, indent=2)


def factcheck_prompt(draft: ScriptDraft) -> str:
    sources = "\n".join(f"- {s.url} ({s.supports})" for s in draft.sources) or "(없음)"
    return (
        f"제목: {draft.title}\n\n장면:\n{scenes_json(draft.scenes)}\n\n작가가 단 출처:\n{sources}"
    )


def rewrite_prompt(scenes: list[DraftScene]) -> str:
    n = char_count(scenes)
    lo, hi = TARGET_CHARS
    goal = (lo + hi) // 2
    direction = "늘려" if n < lo else "줄여"
    return (
        f"지금 내레이션이 공백 포함 {n}자예요. "
        f"{lo}~{hi}자(약 {goal}자)가 되도록 {direction} 주세요.\n\n"
        f"장면:\n{scenes_json(scenes)}"
    )


# ---- 조립 ------------------------------------------------------------------------


def to_script(draft: ScriptDraft, scenes: list[DraftScene], extra: list[SourceRef]) -> Script:
    urls: list[str] = []
    for ref in [*draft.sources, *extra]:
        url = ref.url.strip()
        if url.startswith("http") and url not in urls:
            urls.append(url)
    tags = ["#" + t.strip().lstrip("#") for t in draft.hashtags if t.strip().lstrip("#")]
    tags = ["#왜", *[t for t in tags if t != "#왜"]]
    title = draft.title.strip()
    if "#왜" not in title:
        title = f"{title} #왜"
    return Script(
        title=title,
        scenes=[
            Scene(
                index=i,
                role=s.role,
                narration=s.narration.strip(),
                caption=s.caption.strip() or None,
                image_prompt=s.image_prompt.strip(),
            )
            for i, s in enumerate(scenes)
        ],
        sources=urls,
        hashtags=tags,
    )


def generate(
    topic: Topic,
    *,
    client: Any,
    settings: Settings,
    next_question: str | None = None,
) -> tuple[Script, ScriptReport]:
    """topic 하나로 대본과 검수 기록을 만듭니다. 작업 I(스케줄러)는 next_question 을 넘겨요."""
    model = settings.script_model

    # 1. 초안
    feedback: list[str] = []
    for _ in range(2):
        draft = llm.ask(
            client,
            model=model,
            system=SCRIPT_SYSTEM,
            prompt=draft_prompt(topic, next_question, feedback),
            output=ScriptDraft,
            web_search=True,
        )
        feedback = structure_errors(draft.scenes, draft.sources)
        if not feedback:
            break
    else:
        raise ScriptError("대본이 구성 규칙을 지키지 못했어요: " + "; ".join(feedback))

    scenes = draft.scenes
    warnings: list[str] = []

    # 2. 사실 확인
    check = llm.ask(
        client,
        model=model,
        system=FACTCHECK_SYSTEM,
        prompt=factcheck_prompt(draft),
        output=FactCheck,
        web_search=True,
    )
    wrong = [c for c in check.claims if c.verdict == "incorrect"]
    unverified = [c for c in check.claims if c.verdict == "unverified"]
    revised = False
    if check.revised_scenes:
        problems = structure_errors(check.revised_scenes)
        if problems:
            if wrong:
                raise ScriptError("틀린 주장을 고친 대본의 형식이 깨졌어요: " + "; ".join(problems))
            warnings.append("사실 확인 수정본의 형식이 깨져서 초안을 그대로 썼어요")
        else:
            scenes, revised = check.revised_scenes, True
    elif wrong:
        raise ScriptError(
            "틀린 주장이 있는데 고친 대본이 없어요: " + "; ".join(c.claim for c in wrong)
        )
    if not check.claims:
        warnings.append("사실 확인에서 검증할 주장을 하나도 뽑지 못했어요. 직접 확인해 주세요")
    for c in unverified:
        state = "고친 본에서 약하게 바꿨거나 뺐어요" if revised else "그대로 남아 있어요"
        warnings.append(f"출처를 찾지 못한 주장 ({state}): {c.claim}")
    if draft.confidence == "leading_hypothesis":
        warnings.append(
            "유력한 가설에 기반한 대본이에요. '유력한 설명' 표현이 들어갔는지 확인해 주세요"
        )

    # 3. 길이 맞추기
    rewrites = 0
    lo, hi = TARGET_CHARS
    while not lo <= char_count(scenes) <= hi and rewrites < MAX_REWRITES:
        rewrites += 1
        out = llm.ask(
            client,
            model=model,
            system=REWRITE_SYSTEM,
            prompt=rewrite_prompt(scenes),
            output=Rewrite,
        )
        if structure_errors(out.scenes):
            continue  # 형식이 깨진 고쳐 쓰기는 버리고 한 번 더
        scenes = out.scenes

    script = to_script(draft, scenes, check.extra_sources)
    warnings += script.length_warnings()
    warnings += style_warnings(scenes)

    report = ScriptReport(
        model=model,
        confidence=draft.confidence,
        char_count=char_count(scenes),
        claims=check.claims,
        revised_by_fact_check=revised,
        rewrites=rewrites,
        warnings=warnings,
    )
    return script, report


def run(job: Job) -> Script:
    topic = job.read("topic.json", Topic)
    client = llm.make_client(job.settings)
    script, report = generate(topic, client=client, settings=job.settings)
    job.write(REPORT_FILE, report)
    return script
