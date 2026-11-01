# 숏폼공장 (shortform-factory)

주제 하나를 넣으면 대본, 음성, 이미지, 자막을 합쳐 유튜브 쇼츠 한 편을 만들고, 사람이 검수해 승인하면 예약 업로드까지 하는 파이프라인입니다.

- 포맷: 생활 속 "왜?" 과학, 45~55초, 1080×1920, 해요체
- 구성: 훅 - 맞장구 - 본문 3단계 - 반전 - 마무리 (원고 250~320자)

## 구조

```
topic → script → tts ┐
               visuals ┴→ render → [사람 검수] → upload
```

| 단계 | 모듈 | 결과 파일 | 담당 작업 |
| --- | --- | --- | --- |
| 주제 발굴 | `stages/topic.py` | `topic.json` | B·D |
| 대본 생성 | `stages/script.py` | `script.json`, `script_report.json` | D |
| 음성 | `stages/tts.py` | `narration.json`, `audio/` | E |
| 이미지 | `stages/visuals.py` | `visuals.json`, `visuals/` | F |
| 영상 합성 | `stages/render.py` | `render.json`, mp4 | G |
| 업로드 | `stages/upload.py` | `upload.json` | H |

각 단계는 `output/<job_id>/` 폴더에 결과를 JSON으로 남기고, 다음 단계는 그 파일을 읽습니다. 데이터 형식은 `src/shortform/models.py` 한 곳에 정의되어 있어서, 단계마다 따로 만들고 고칠 수 있습니다. topic·script는 구현되어 있고, 나머지 단계는 작업 E~H에서 채웁니다.

## 대본 생성 (작업 D)

- `topic`: Claude Haiku 5.5가 주제 힌트를 "~는 왜 ~할까?" 질문으로 다듬어요. 제외 주제(치료·투자·정치 등)이거나 최근 90일 안의 주제와 같은 현상이면 멈춰요.
- `script`: Claude Sonnet 5.5가 웹 검색을 켜고 장면 7개(훅-맞장구-본문3-반전-마무리)를 씁니다. 이어서 별도 호출이 주장을 하나씩 검증해 틀린 곳을 고치고, 원고가 250~320자(공백 포함)를 벗어나면 최대 2번 고쳐 씁니다.
- 검수자는 `script_report.json`에서 주장별 검증 결과(supported·incorrect·unverified), 근거 URL, 길이·문장 길이 경고를 확인하면 돼요.
- 모델은 `.env`의 `SHORTFORM_SCRIPT_MODEL`, `SHORTFORM_TOPIC_MODEL`로 바꿀 수 있어요. 프롬프트는 `stages/prompts.py`에 있어요.

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # API 키 채우기
```

FFmpeg가 필요합니다 (`brew install ffmpeg` 또는 `apt install ffmpeg`).

## 사용법

```bash
shortform new "하품은 왜 옮을까"      # 작업 폴더 생성, job_id 출력
shortform run <job_id>                # render 까지 실행 (검수 전에서 멈춤)
shortform status <job_id>             # 단계별 상태
shortform approve <job_id>            # 검수 통과
shortform run <job_id> --from upload  # 업로드
```

실패한 단계는 `--from <단계>`로 그 단계부터 다시 돌리면 됩니다.

## 개발

```bash
ruff check . && ruff format --check . && pytest -q
```

비밀키는 `.env`에만 두고 커밋하지 않습니다. `client_secret*.json`, `token*.json`, `output/`도 git에서 제외되어 있습니다.
