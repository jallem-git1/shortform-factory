"""이미지 소스: 장면별 이미지를 스톡 API 에서 구하고 저작권 정보를 기록합니다. (작업 F)

장면마다 image_prompt 로 Pexels 를 먼저 찾고, 없으면 Pixabay 를 찾습니다.
키가 있는 서비스만 씁니다. 결과가 없으면 검색어를 앞쪽 단어 몇 개로 줄여 한 번 더 찾습니다.
같은 영상 안에서는 같은 사진을 두 번 쓰지 않습니다.

파일은 job.subdir('visuals') 에 scene_<번호>_<출처>_<사진id>.jpg 로 저장.
license_note 에는 원본 페이지 URL, 작가, 라이선스를 적습니다 (업로드 설명란 출처 표기용).

읽는 파일: script.json
돌려주는 것: Visuals
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ..models import Scene, Script, Visual, Visuals
from ..pipeline import Job

TARGET_W, TARGET_H = 1080, 1920
USER_AGENT = "shortform-factory/0.1 (+https://github.com/jallem-git1/shortform-factory)"
TIMEOUT = 30

# (url, headers) -> 응답 본문. 테스트에서 가짜로 바꿔 끼웁니다.
HttpGet = Callable[[str, dict[str, str]], bytes]


def http_get(url: str, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


@dataclass(frozen=True)
class Candidate:
    """검색 결과 사진 하나."""

    source: str
    id: str
    width: int
    height: int
    download_url: str
    page_url: str
    author: str
    author_url: str
    license: str

    @property
    def license_note(self) -> str:
        by = f"{self.author} ({self.author_url})" if self.author_url else self.author
        return f"{self.page_url} | 작가: {by} | {self.license}"

    def score(self) -> float:
        """9:16 비율에 가깝고 세로 1920 이상일수록 좋음 (작을수록 좋은 점수)."""
        ratio_gap = abs(self.width / self.height - TARGET_W / TARGET_H)
        too_small = 1.0 if self.height < TARGET_H else 0.0
        return ratio_gap + too_small


class Provider:
    name: str

    def search(self, query: str, get: HttpGet) -> list[Candidate]:
        raise NotImplementedError


class Pexels(Provider):
    """https://www.pexels.com/api/documentation/ — 무료, 출처 표기 권장."""

    name = "pexels"
    LICENSE = "Pexels License (https://www.pexels.com/license/)"

    def __init__(self, api_key: str, per_page: int = 15):
        self.api_key = api_key
        self.per_page = per_page

    def search(self, query: str, get: HttpGet) -> list[Candidate]:
        params = {"query": query, "orientation": "portrait", "per_page": self.per_page}
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(params)
        data = json.loads(get(url, {"Authorization": self.api_key}))
        out = []
        for p in data.get("photos", []):
            # 원본에 Pexels 이미지 CDN 파라미터를 붙여 1080x1920 으로 잘라 받습니다.
            crop = f"?auto=compress&cs=tinysrgb&fit=crop&w={TARGET_W}&h={TARGET_H}"
            out.append(
                Candidate(
                    source=self.name,
                    id=str(p["id"]),
                    width=p["width"],
                    height=p["height"],
                    download_url=p["src"]["original"] + crop,
                    page_url=p["url"],
                    author=p.get("photographer", ""),
                    author_url=p.get("photographer_url", ""),
                    license=self.LICENSE,
                )
            )
        return out


class Pixabay(Provider):
    """https://pixabay.com/api/docs/ — 무료, 핫링크 금지라 내려받아 씁니다."""

    name = "pixabay"
    LICENSE = "Pixabay Content License (https://pixabay.com/service/license-summary/)"

    def __init__(self, api_key: str, per_page: int = 20):
        self.api_key = api_key
        self.per_page = per_page

    def search(self, query: str, get: HttpGet) -> list[Candidate]:
        params = {
            "key": self.api_key,
            "q": query[:100],  # API 제한: 100자
            "image_type": "photo",
            "orientation": "vertical",
            "safesearch": "true",
            "per_page": self.per_page,
        }
        url = "https://pixabay.com/api/?" + urllib.parse.urlencode(params)
        data = json.loads(get(url, {}))
        out = []
        for h in data.get("hits", []):
            user = h.get("user", "")
            out.append(
                Candidate(
                    source=self.name,
                    id=str(h["id"]),
                    width=h["imageWidth"],
                    height=h["imageHeight"],
                    download_url=h["largeImageURL"],
                    page_url=h["pageURL"],
                    author=user,
                    author_url=f"https://pixabay.com/users/{user}-{h['user_id']}/" if user else "",
                    license=self.LICENSE,
                )
            )
        return out


class NoImageFound(RuntimeError):
    pass


def providers_from_settings(job: Job) -> list[Provider]:
    s = job.settings
    providers: list[Provider] = []
    if s.pexels_api_key:
        providers.append(Pexels(s.pexels_api_key.get_secret_value()))
    if s.pixabay_api_key:
        providers.append(Pixabay(s.pixabay_api_key.get_secret_value()))
    if not providers:
        raise RuntimeError(".env 에 PEXELS_API_KEY 나 PIXABAY_API_KEY 중 하나는 있어야 해요")
    return providers


def queries_for(scene: Scene) -> list[str]:
    """원래 검색어, 그다음 앞쪽 단어 3개로 줄인 검색어."""
    full = re.sub(r"\s+", " ", scene.image_prompt).strip()
    short = " ".join(re.sub(r"[^\w\s]", " ", full).split()[:3])
    return [q for q in dict.fromkeys([full, short]) if q]


def pick(
    scene: Scene, providers: Iterable[Provider], used: set[tuple[str, str]], get: HttpGet
) -> Candidate:
    tried = []
    for query in queries_for(scene):
        for provider in providers:
            results = [
                c
                for c in provider.search(query, get)
                if c.height >= c.width and (c.source, c.id) not in used
            ]
            tried.append(f"{provider.name}:'{query}'")
            if results:
                return min(results, key=Candidate.score)
    raise NoImageFound(f"장면 {scene.index} 이미지를 못 찾았어요 ({', '.join(tried)})")


def download(c: Candidate, scene_index: int, out_dir: Path, get: HttpGet) -> Path:
    path = out_dir / f"scene_{scene_index:02d}_{c.source}_{c.id}.jpg"
    if not path.exists():  # 다시 돌릴 때 같은 사진이면 재사용
        path.write_bytes(get(c.download_url, {}))
    return path


def collect(
    script: Script, out_dir: Path, providers: list[Provider], get: HttpGet | None = None
) -> Visuals:
    get = get or http_get
    used: set[tuple[str, str]] = set()
    items = []
    for scene in sorted(script.scenes, key=lambda s: s.index):
        c = pick(scene, providers, used, get)
        used.add((c.source, c.id))
        items.append(
            Visual(
                scene_index=scene.index,
                path=download(c, scene.index, out_dir, get),
                source=c.source,
                license_note=c.license_note,
            )
        )
    return Visuals(items=items)


def run(job: Job) -> Visuals:
    script = job.read("script.json", Script)
    return collect(script, job.subdir("visuals"), providers_from_settings(job))
