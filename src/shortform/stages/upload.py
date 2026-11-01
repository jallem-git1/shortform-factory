"""업로드: YouTube Data API 로 예약 업로드합니다. (작업 H)

검수 승인(job.state.approved) 후에만 실행됩니다.

읽는 파일: script.json, render.json
돌려주는 것: UploadResult
"""

from ..models import UploadResult
from ..pipeline import Job


def run(job: Job) -> UploadResult:
    raise NotImplementedError("작업 H 에서 구현할 예정이에요")
