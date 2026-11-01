import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from shortform import pipeline
from shortform.config import Settings
from shortform.models import Scene, SceneRole, Script, Visuals
from shortform.stages import visuals


def pexels_photo(pid: int, w: int = 3000, h: int = 4500) -> dict:
    return {
        "id": pid,
        "width": w,
        "height": h,
        "url": f"https://www.pexels.com/photo/x-{pid}/",
        "photographer": "Kim",
        "photographer_url": "https://www.pexels.com/@kim",
        "src": {"original": f"https://images.pexels.com/photos/{pid}/x.jpeg"},
    }


def pixabay_hit(pid: int) -> dict:
    return {
        "id": pid,
        "imageWidth": 1080,
        "imageHeight": 1920,
        "pageURL": f"https://pixabay.com/photos/x-{pid}/",
        "largeImageURL": f"https://pixabay.com/get/{pid}.jpg",
        "user": "lee",
        "user_id": 7,
    }


class FakeHttp:
    """API 응답을 query 에 따라 돌려주고, 이미지 요청에는 가짜 바이트를 줍니다."""

    def __init__(self, pexels: dict[str, list[dict]], pixabay: dict[str, list[dict]]):
        self.pexels = pexels
        self.pixabay = pixabay
        self.calls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str]) -> bytes:
        self.calls.append(url)
        u = urlparse(url)
        q = parse_qs(u.query)
        if u.netloc == "api.pexels.com":
            assert headers["Authorization"] == "pk"
            assert q["orientation"] == ["portrait"]
            return json.dumps({"photos": self.pexels.get(q["query"][0], [])}).encode()
        if u.netloc == "pixabay.com" and u.path == "/api/":
            assert q["key"] == ["xk"] and q["orientation"] == ["vertical"]
            return json.dumps({"hits": self.pixabay.get(q["q"][0], [])}).encode()
        return b"JPEG:" + url.encode()


def make_script(*prompts: str) -> Script:
    return Script(
        title="t",
        scenes=[
            Scene(index=i, role=SceneRole.BODY, narration="문장이에요", image_prompt=p)
            for i, p in enumerate(prompts)
        ],
    )


PROVIDERS = [visuals.Pexels("pk"), visuals.Pixabay("xk")]


def test_prefers_pexels_and_records_license(tmp_path: Path) -> None:
    http = FakeHttp({"yawning cat": [pexels_photo(1)]}, {"yawning cat": [pixabay_hit(9)]})
    result = visuals.collect(make_script("yawning cat"), tmp_path, PROVIDERS, http)

    v = result.items[0]
    assert v.source == "pexels"
    assert v.path == tmp_path / "scene_00_pexels_1.jpg"
    assert v.path.read_bytes().startswith(b"JPEG:https://images.pexels.com/photos/1/x.jpeg?")
    assert "fit=crop&w=1080&h=1920" in v.path.read_bytes().decode()
    assert "https://www.pexels.com/photo/x-1/" in v.license_note
    assert "Kim" in v.license_note and "Pexels License" in v.license_note


def test_falls_back_to_pixabay_then_short_query(tmp_path: Path) -> None:
    http = FakeHttp(
        {},
        {"brain neurons glowing": [pixabay_hit(5)]},
    )
    script = make_script("brain neurons glowing, cinematic close-up, blue light")
    v = visuals.collect(script, tmp_path, PROVIDERS, http).items[0]

    assert v.source == "pixabay"
    assert "https://pixabay.com/photos/x-5/" in v.license_note
    assert "https://pixabay.com/users/lee-7/" in v.license_note
    assert "Pixabay Content License" in v.license_note


def test_no_duplicate_photos_and_portrait_only(tmp_path: Path) -> None:
    landscape = pexels_photo(3, w=4000, h=2000)
    http = FakeHttp({"sky": [landscape, pexels_photo(1), pexels_photo(2)]}, {})
    result = visuals.collect(make_script("sky", "sky"), tmp_path, PROVIDERS, http)

    ids = [v.path.name for v in result.items]
    assert ids == ["scene_00_pexels_1.jpg", "scene_01_pexels_2.jpg"]


def test_picks_closest_to_9_16(tmp_path: Path) -> None:
    square_ish = pexels_photo(1, w=3000, h=3200)
    tall = pexels_photo(2, w=2160, h=3840)
    http = FakeHttp({"sea": [square_ish, tall]}, {})
    v = visuals.collect(make_script("sea"), tmp_path, PROVIDERS, http).items[0]
    assert v.path.name == "scene_00_pexels_2.jpg"


def test_raises_when_nothing_found(tmp_path: Path) -> None:
    with pytest.raises(visuals.NoImageFound, match="장면 0"):
        visuals.collect(make_script("zzz"), tmp_path, PROVIDERS, FakeHttp({}, {}))


def test_run_needs_a_key(tmp_path: Path) -> None:
    settings = Settings(SHORTFORM_OUTPUT_DIR=tmp_path, PEXELS_API_KEY=None, PIXABAY_API_KEY=None)
    job = pipeline.Job.create("테스트", settings)
    job.write("script.json", make_script("x"))
    with pytest.raises(RuntimeError, match="PEXELS_API_KEY"):
        visuals.run(job)


def test_run_writes_into_job_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(SHORTFORM_OUTPUT_DIR=tmp_path, PEXELS_API_KEY="pk", PIXABAY_API_KEY=None)
    job = pipeline.Job.create("테스트", settings)
    job.write("script.json", make_script("yawning cat"))
    monkeypatch.setattr(visuals, "http_get", FakeHttp({"yawning cat": [pexels_photo(1)]}, {}))

    result = visuals.run(job)
    assert isinstance(result, Visuals)
    assert result.items[0].path.parent == job.dir / "visuals"
