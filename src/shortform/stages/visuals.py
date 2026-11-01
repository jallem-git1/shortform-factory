"""이미지 소스: 장면별 이미지를 스톡 API 또는 AI 생성으로 구하고 저작권 정보를 기록합니다. (작업 F)

파일은 job.subdir('visuals') 에 저장.

읽는 파일: script.json
돌려주는 것: Visuals
"""

from ..models import Visuals
from ..pipeline import Job


def run(job: Job) -> Visuals:
    raise NotImplementedError("작업 F 에서 구현할 예정이에요")
