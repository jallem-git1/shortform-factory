"""영상 합성: FFmpeg 로 1080x1920 영상에 이미지, 음성, 자막, 배경음악을 합칩니다. (작업 G)

읽는 파일: script.json, narration.json, visuals.json
돌려주는 것: RenderResult
"""

from ..models import RenderResult
from ..pipeline import Job


def run(job: Job) -> RenderResult:
    raise NotImplementedError("작업 G 에서 구현할 예정이에요")
