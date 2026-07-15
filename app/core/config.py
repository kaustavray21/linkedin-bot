from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # MySQL
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_database: str = "linkedin_bot"
    mysql_user: str = "root"
    mysql_password: str = ""

    # LinkedIn OAuth
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8000/auth/callback"
    app_url: str = "http://localhost:8000"

    # LinkedIn API
    linkedin_api_base: str = "https://api.linkedin.com/v2"
    linkedin_auth_url: str = "https://www.linkedin.com/oauth/v2/authorization"
    linkedin_token_url: str = "https://www.linkedin.com/oauth/v2/accessToken"

    # AI
    openai_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_image_model: str = "gemini-2.5-flash-image"

    # File storage
    uploads_dir: str = "app/static/uploads"

    # Logging
    log_level: str = "INFO"

    # fal.ai
    fal_api_key: str = ""
    fal_image_model: str = "fal-ai/nano-banana-2"

    # App
    debug: bool = False

    # Session secret for state param
    session_secret: str = "change-me-in-production"


settings = Settings()
