from pydantic import BaseModel, Field, HttpUrl, field_validator


class MagnetSubmission(BaseModel):
    magnet_uri: str = Field(min_length=20, max_length=8192)
    source_url: HttpUrl | None = None

    @field_validator("magnet_uri")
    @classmethod
    def validate_magnet(cls, value: str) -> str:
        if not value.startswith("magnet:?"):
            raise ValueError("A valid magnet URI is required")
        lowered = value.lower()
        if "xt=urn:btih:" not in lowered and "xt=urn:btmh:" not in lowered:
            raise ValueError("Magnet must contain a BitTorrent info-hash")
        return value
