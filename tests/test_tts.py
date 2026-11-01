import base64
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from shortform import pipeline
from shortform.config import Settings
from shortform.models import Narration, Scene, SceneRole, Script
from shortform.stages import tts


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(SHORTFORM_OUTPUT_DIR=tmp_path, _env_file=None)


@pytest.fixture
def job(settings: Settings) -> pipeline.Job:
    job = pipeline.Job.create("하품은 왜 옮을까", settings)
    job.write(
        "script.json",
        Script(
            title="하품",
            scenes=[
                Scene(
                    index=0, role=SceneRole.HOOK, narration="하품, 왜 옮을까요?", image_prompt="x"
                ),
                Scene(index=1, role=SceneRole.NOD, narration="   ", image_prompt="x"),
                Scene(index=2, role=SceneRole.BODY, narration="뇌가 따라 해요.", image_prompt="x"),
            ],
        ),
    )
    return job


def test_run_writes_audio_and_estimates_timings(job, monkeypatch) -> None:
    calls = []

    def fake(text: str, settings: Settings) -> tts.Synth:
        calls.append(text)
        return tts.Synth(audio=b"ID3fake")

    monkeypatch.setitem(tts.PROVIDERS, "google", fake)
    monkeypatch.setattr(tts, "audio_duration", lambda path: 2.0)
    job.settings.tts_provider = "google"

    result = tts.run(job)

    assert isinstance(result, Narration)
    assert calls == ["하품, 왜 옮을까요?", "뇌가 따라 해요."]  # 빈 장면은 건너뜀
    assert [c.scene_index for c in result.clips] == [0, 2]
    assert (job.dir / "audio" / "scene_00.mp3").read_bytes() == b"ID3fake"
    words = result.clips[0].word_timings
    assert [w for w, _, _ in words] == ["하품,", "왜", "옮을까요?"]
    assert words[0][1] == 0.0 and words[-1][2] == pytest.approx(2.0)
    assert result.total_seconds == pytest.approx(4.0)


def test_provider_timings_are_kept(job, monkeypatch) -> None:
    timings = [("하품,", 0.1, 0.5)]
    monkeypatch.setitem(tts.PROVIDERS, "edge", lambda t, s: tts.Synth(b"x", timings))
    monkeypatch.setattr(tts, "audio_duration", lambda path: 1.0)
    job.settings.tts_provider = "edge"
    assert tts.run(job).clips[0].word_timings == timings


def test_auto_picks_google_only_with_key(settings: Settings) -> None:
    assert tts.resolve_provider(settings) == "edge"
    settings.google_tts_api_key = SecretStr("k")
    assert tts.resolve_provider(settings) == "google"
    settings.tts_provider = "nope"
    with pytest.raises(tts.TTSError):
        tts.resolve_provider(settings)


def test_estimate_word_timings_are_ordered() -> None:
    out = tts.estimate_word_timings("공기가 차가워지면 하품이 나요. 정말요?", 5.0)
    assert len(out) == 5
    for (_, s, e), (_, s2, _) in zip(out, out[1:], strict=False):
        assert s < e <= s2
    assert out[-1][2] == pytest.approx(5.0)
    assert out[4][1] > out[3][2]  # 마침표 뒤 쉼


def test_words_from_chars() -> None:
    chars = list("왜 옮아요")
    starts = [0.0, 0.2, 0.3, 0.4, 0.5]
    ends = [0.2, 0.3, 0.4, 0.5, 0.6]
    assert tts.words_from_chars(chars, starts, ends) == [("왜", 0.0, 0.2), ("옮아요", 0.3, 0.6)]


def test_google_request(settings: Settings, monkeypatch) -> None:
    settings.google_tts_api_key = SecretStr("secret")
    seen = {}

    def post(url, params, json, timeout):
        seen.update(url=url, params=params, json=json)
        content = base64.b64encode(b"mp3").decode()
        return httpx.Response(
            200, json={"audioContent": content}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(tts.httpx, "post", post)
    out = tts.synth_google("안녕하세요", settings)
    assert out.audio == b"mp3" and out.word_timings is None
    assert seen["params"] == {"key": "secret"}
    assert seen["json"]["voice"]["name"] == "ko-KR-Chirp3-HD-Aoede"


def test_elevenlabs_needs_voice_and_parses_alignment(settings: Settings, monkeypatch) -> None:
    settings.elevenlabs_api_key = SecretStr("k")
    with pytest.raises(tts.TTSError):
        tts.synth_elevenlabs("왜", settings)

    settings.tts_voice = "voice123"

    def post(url, params, headers, json, timeout):
        assert "voice123" in url
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"a").decode(),
                "alignment": {
                    "characters": list("왜 요"),
                    "character_start_times_seconds": [0.0, 0.1, 0.2],
                    "character_end_times_seconds": [0.1, 0.2, 0.3],
                },
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tts.httpx, "post", post)
    assert tts.synth_elevenlabs("왜 요", settings).word_timings == [
        ("왜", 0.0, 0.1),
        ("요", 0.2, 0.3),
    ]


def test_client_errors_are_not_retried(settings: Settings, monkeypatch) -> None:
    calls = []

    def bad(text, s):
        calls.append(1)
        raise tts.TTSError("Google TTS 403: denied", retryable=False)

    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    with pytest.raises(tts.TTSError):
        tts._with_retry(bad, "x", settings)
    assert len(calls) == 1


def test_server_errors_are_retried(settings: Settings, monkeypatch) -> None:
    calls = []

    def flaky(text, s):
        calls.append(1)
        if len(calls) < 3:
            raise tts.TTSError("503", retryable=True)
        return tts.Synth(b"ok")

    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    assert tts._with_retry(flaky, "x", settings).audio == b"ok"
