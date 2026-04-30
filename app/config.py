from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Bookarr"
    app_version: str = Field(default="0.1", validation_alias="BOOKARR_VERSION")
    secret_key: str = Field(default="change-me", validation_alias="BOOKARR_SECRET_KEY")
    requester_password: str = Field(default="requester", validation_alias="BOOKARR_REQUESTER_PASSWORD")
    admin_password: str = Field(default="admin", validation_alias="BOOKARR_ADMIN_PASSWORD")
    database_url: str = Field(default="sqlite:////data/bookarr.db", validation_alias="BOOKARR_DATABASE_URL")

    listenarr_url: str = Field(default="http://listenarr:8787", validation_alias="LISTENARR_URL")
    listenarr_token: str = Field(default="", validation_alias="LISTENARR_TOKEN")
    listenarr_auth_mode: str = Field(default="x-api-key", validation_alias="LISTENARR_AUTH_MODE")
    listenarr_api_key_name: str = Field(default="apikey", validation_alias="LISTENARR_API_KEY_NAME")
    listenarr_search_path: str = Field(default="/api/v1/search/intelligent", validation_alias="LISTENARR_SEARCH_PATH")
    listenarr_search_query_param: str = Field(default="query", validation_alias="LISTENARR_SEARCH_QUERY_PARAM")
    listenarr_search_region: str = Field(default="us", validation_alias="LISTENARR_SEARCH_REGION")
    listenarr_request_path: str = Field(default="/api/v1/library/add", validation_alias="LISTENARR_REQUEST_PATH")
    listenarr_antiforgery_path: str = Field(
        default="/api/v1/antiforgery/token",
        validation_alias="LISTENARR_ANTIFORGERY_PATH",
    )
    listenarr_status_path: str = Field(default="/api/v1/library/{listenarr_id}", validation_alias="LISTENARR_STATUS_PATH")
    status_poll_seconds: int = Field(default=300, validation_alias="BOOKARR_STATUS_POLL_SECONDS")
    completed_retention_days: int = Field(default=30, validation_alias="BOOKARR_COMPLETED_RETENTION_DAYS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
