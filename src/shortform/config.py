"""환경 변수(.env)에서 읽는 설정. 비밀키는 코드에 적지 않고 여기로만 들어옵니다."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 공통
    output_dir: Path = Field(Path("output"), alias="SHORTFORM_OUTPUT_DIR")

    # 대본 (D)
    anthropic_api_key: SecretStr | None = Field(None, alias="ANTHROPIC_API_KEY")
    script_model: str = Field("claude-sonnet-5-5", alias="SHORTFORM_SCRIPT_MODEL")

    # 음성 (E)
    tts_provider: str = Field("auto", alias="SHORTFORM_TTS_PROVIDER")
    tts_voice: str | None = Field(None, alias="SHORTFORM_TTS_VOICE")
    tts_rate: float = Field(1.0, alias="SHORTFORM_TTS_RATE", description="말 속도 배율")
    google_tts_api_key: SecretStr | None = Field(None, alias="GOOGLE_TTS_API_KEY")
    elevenlabs_api_key: SecretStr | None = Field(None, alias="ELEVENLABS_API_KEY")

    # 이미지 (F)
    pexels_api_key: SecretStr | None = Field(None, alias="PEXELS_API_KEY")
    pixabay_api_key: SecretStr | None = Field(None, alias="PIXABAY_API_KEY")

    # 업로드 (H)
    youtube_client_secret_file: Path = Field(
        Path("client_secret.json"), alias="YOUTUBE_CLIENT_SECRET_FILE"
    )


def load_settings() -> Settings:
    return Settings()
