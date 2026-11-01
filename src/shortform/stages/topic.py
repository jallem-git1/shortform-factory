"""주제 발굴: job.state.topic_hint 를 '왜?' 질문 하나로 다듬습니다. (작업 B·D)

읽는 파일: job.json
돌려주는 것: Topic
"""

from ..models import Topic
from ..pipeline import Job


def run(job: Job) -> Topic:
    raise NotImplementedError("작업 B·D 에서 구현할 예정이에요")
