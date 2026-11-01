"""음성: 장면별 내레이션을 TTS 로 읽고 단어 타임스탬프를 뽑습니다. (작업 E)

오디오는 job.subdir('audio') 에 저장.

읽는 파일: script.json
돌려주는 것: Narration
"""

from ..models import Narration
from ..pipeline import Job


def run(job: Job) -> Narration:
    raise NotImplementedError("작업 E 에서 구현할 예정이에요")
