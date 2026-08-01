from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "STL Sniffer"
    app_env: str = "production"
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str
    meili_index: str = "torrents"
    max_torrent_bytes: int = 5 * 1024 * 1024
    magnet_resolve_timeout_seconds: int = 75
    submission_rate_limit_per_hour: int = 30
    source_feeds: str = ""
    source_poll_interval_seconds: int = 900
    model_extensions: str = ".stl,.3mf,.obj,.step,.stp,.iges,.igs,.scad"
    data_dir: Path = Path("/data")

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    @property
    def source_feed_list(self) -> list[str]:
        return [item.strip() for item in self.source_feeds.split(",") if item.strip()]

    @property
    def model_extension_set(self) -> set[str]:
        return {
            item.strip().lower() if item.strip().startswith(".") else f".{item.strip().lower()}"
            for item in self.model_extensions.split(",")
            if item.strip()
        }

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    return settings
