"""대본 생성: Claude API 로 장면별 내레이션과 이미지 프롬프트를 만들고 출처를 남깁니다. (작업 D)

읽는 파일: topic.json
돌려주는 것: Script
"""

from ..models import Script
from ..pipeline import Job


def run(job: Job) -> Script:
    raise NotImplementedError("작업 D 에서 구현할 예정이에요")
