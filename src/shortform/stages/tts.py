"""음성: 장면별 내레이션을 TTS 로 읽고 단어 타임스탬프를 뽑습니다. (작업 E)

오디오는 job.subdir('audio') 에 scene_00.mp3 처럼 장면마다 하나씩 저장합니다.

읽는 파일: script.json
돌려주는 것: Narration

제공자는 SHORTFORM_TTS_PROVIDER 로 고릅니다.

- auto (기본): GOOGLE_TTS_API_KEY 가 있으면 google, 없으면 edge
- google: Google Cloud TTS Chirp 3 HD. 월 100만 자 무료, 한국어 품질 좋음.
  단어 타임스탬프를 주지 않아서 음절 수 비례로 추정합니다.
- edge: Microsoft Edge 읽어 주기(edge-tts). 키 없이 무료지만 비공식 경로라
  언제든 막힐 수 있고 상업 이용 허가가 없어요. 개발·시험용.
- elevenlabs: 가장 자연스럽고 글자 단위 타임스탬프를 줌. 유료.

SHORTFORM_TTS_VOICE 를 비우면 제공자별 기본 목소리를 씁니다.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import Settings
from ..models import TARGET_SECONDS, AudioClip, Narration, Script
from ..pipeline import Job

log = logging.getLogger(__name__)

WordTiming = tuple[str, float, float]

DEFAULT_VOICES = {
    "google": "ko-KR-Chirp3-HD-Aoede",
    "edge": "ko-KR-SunHiNeural",
    # ElevenLabs 는 계정마다 목소리 ID 가 달라서 기본값이 없어요.
}

GOOGLE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
ELEVENLABS_MODEL = "eleven_multilingual_v2"


@dataclass
class Synth:
    """한 장면의 합성 결과. word_timings 가 None 이면 추정으로 채웁니다."""

    audio: bytes
    word_timings: list[WordTiming] | None = None


Provider = Callable[[str, Settings], Synth]


class TTSError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# ---------------------------------------------------------------- 제공자


def synth_google(text: str, settings: Settings) -> Synth:
    if settings.google_tts_api_key is None:
        raise TTSError("GOOGLE_TTS_API_KEY 가 비어 있어요")
    voice = settings.tts_voice or DEFAULT_VOICES["google"]
    body = {
        "input": {"text": text},
        "voice": {"languageCode": "ko-KR", "name": voice},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": settings.tts_rate},
    }
    r = httpx.post(
        GOOGLE_URL,
        params={"key": settings.google_tts_api_key.get_secret_value()},
        json=body,
        timeout=60,
    )
    _raise_for_status(r, "Google TTS")
    return Synth(audio=base64.b64decode(r.json()["audioContent"]))


def synth_edge(text: str, settings: Settings) -> Synth:
    import edge_tts

    voice = settings.tts_voice or DEFAULT_VOICES["edge"]
    rate = f"{round((settings.tts_rate - 1) * 100):+d}%"

    async def go() -> Synth:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        audio = bytearray()
        words: list[WordTiming] = []
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7  # 100ns 단위
                end = start + chunk["duration"] / 1e7
                words.append((chunk["text"], round(start, 3), round(end, 3)))
        return Synth(audio=bytes(audio), word_timings=words or None)

    return asyncio.run(go())


def synth_elevenlabs(text: str, settings: Settings) -> Synth:
    if settings.elevenlabs_api_key is None:
        raise TTSError("ELEVENLABS_API_KEY 가 비어 있어요")
    if not settings.tts_voice:
        raise TTSError("ElevenLabs 는 SHORTFORM_TTS_VOICE 에 목소리 ID 를 넣어야 해요")
    r = httpx.post(
        ELEVENLABS_URL.format(voice=settings.tts_voice),
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
        json={
            "text": text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": {"speed": settings.tts_rate},
        },
        timeout=120,
    )
    _raise_for_status(r, "ElevenLabs")
    data = r.json()
    a = data.get("alignment") or {}
    words = words_from_chars(
        a.get("characters", []),
        a.get("character_start_times_seconds", []),
        a.get("character_end_times_seconds", []),
    )
    return Synth(audio=base64.b64decode(data["audio_base64"]), word_timings=words or None)


PROVIDERS: dict[str, Provider] = {
    "google": synth_google,
    "edge": synth_edge,
    "elevenlabs": synth_elevenlabs,
}


def resolve_provider(settings: Settings) -> str:
    name = settings.tts_provider.strip().lower()
    if name == "auto":
        return "google" if settings.google_tts_api_key else "edge"
    if name not in PROVIDERS:
        raise TTSError(f"모르는 TTS 제공자 '{name}' (auto | {' | '.join(PROVIDERS)})")
    return name


# ---------------------------------------------------------------- 타임스탬프


def words_from_chars(chars: list[str], starts: list[float], ends: list[float]) -> list[WordTiming]:
    """글자 단위 정렬을 띄어쓰기 기준 단어로 묶습니다."""
    words: list[WordTiming] = []
    buf, w_start, w_end = "", 0.0, 0.0
    for ch, s, e in zip(chars, starts, ends, strict=False):
        if ch.isspace():
            if buf:
                words.append((buf, round(w_start, 3), round(w_end, 3)))
                buf = ""
            continue
        if not buf:
            w_start = s
        buf += ch
        w_end = e
    if buf:
        words.append((buf, round(w_start, 3), round(w_end, 3)))
    return words


_PAUSE = re.compile(r"[.?!…,]$")


def estimate_word_timings(text: str, duration: float) -> list[WordTiming]:
    """타임스탬프를 안 주는 제공자용. 한국어는 음절 길이가 고른 편이라
    단어마다 글자 수에 비례해 시간을 나누고, 문장부호 뒤에는 쉼을 조금 줍니다."""
    words = text.split()
    if not words or duration <= 0:
        return []
    weights = [len(re.sub(r"[^\w]", "", w)) or 1 for w in words]
    pauses = [1.5 if _PAUSE.search(w) else 0.0 for w in words]
    pauses[-1] = 0.0
    unit = duration / (sum(weights) + sum(pauses))
    out: list[WordTiming] = []
    t = 0.0
    for w, wt, p in zip(words, weights, pauses, strict=True):
        end = t + wt * unit
        out.append((w, round(t, 3), round(end, 3)))
        t = end + p * unit
    return out


# ---------------------------------------------------------------- 단계


def audio_duration(path: Path) -> float:
    from mutagen.mp3 import MP3

    return float(MP3(path).info.length)


def _raise_for_status(r: httpx.Response, who: str) -> None:
    if r.is_error:
        retryable = r.status_code == 429 or r.status_code >= 500
        raise TTSError(f"{who} {r.status_code}: {r.text[:300]}", retryable=retryable)


def _with_retry(fn: Provider, text: str, settings: Settings, tries: int = 3) -> Synth:
    for attempt in range(1, tries + 1):
        try:
            return fn(text, settings)
        except TTSError as e:
            # 키 없음, 잘못된 요청 같은 오류는 다시 해도 같아요
            if not e.retryable or attempt == tries:
                raise
        except Exception:
            if attempt == tries:
                raise
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def run(job: Job) -> Narration:
    script = job.read("script.json", Script)
    settings = job.settings
    name = resolve_provider(settings)
    provider = PROVIDERS[name]
    out_dir = job.subdir("audio")
    log.info("TTS 제공자: %s", name)

    clips: list[AudioClip] = []
    for scene in script.scenes:
        text = scene.narration.strip()
        if not text:
            continue
        synth = _with_retry(provider, text, settings)
        path = out_dir / f"scene_{scene.index:02d}.mp3"
        path.write_bytes(synth.audio)
        duration = audio_duration(path)
        timings = synth.word_timings or estimate_word_timings(text, duration)
        clips.append(
            AudioClip(
                scene_index=scene.index,
                path=path,
                duration=round(duration, 3),
                word_timings=timings,
            )
        )

    narration = Narration(clips=clips)
    lo, hi = TARGET_SECONDS
    total = narration.total_seconds
    if not lo <= total <= hi:
        log.warning(
            "내레이션이 %.1f초예요 (목표 %d~%d초). SHORTFORM_TTS_RATE 로 속도를 조절할 수 있어요",
            total,
            lo,
            hi,
        )
    return narration
