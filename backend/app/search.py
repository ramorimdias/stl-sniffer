from __future__ import annotations

import httpx

from .config import get_settings
from .models import Torrent


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.meili_master_key}",
        "Content-Type": "application/json",
    }


def ensure_index() -> None:
    settings = get_settings()
    with httpx.Client(timeout=5) as client:
        response = client.post(
            f"{settings.meili_url}/indexes",
            headers=_headers(),
            json={"uid": settings.meili_index, "primaryKey": "id"},
        )
        if response.status_code not in {200, 201, 202, 409}:
            response.raise_for_status()
        client.put(
            f"{settings.meili_url}/indexes/{settings.meili_index}/settings/searchable-attributes",
            headers=_headers(),
            json=["name", "formats", "model_paths"],
        ).raise_for_status()
        client.put(
            f"{settings.meili_url}/indexes/{settings.meili_index}/settings/filterable-attributes",
            headers=_headers(),
            json=["formats", "status"],
        ).raise_for_status()


def torrent_document(torrent: Torrent) -> dict:
    return {
        "id": torrent.id,
        "name": torrent.name,
        "formats": torrent.formats,
        "model_file_count": torrent.model_file_count,
        "file_count": torrent.file_count,
        "total_size": torrent.total_size,
        "status": torrent.status,
        "source_url": torrent.source_url,
        "discovered_at": torrent.discovered_at.isoformat(),
        "model_paths": [item.path for item in torrent.files if item.is_model][:200],
    }


def index_torrent(torrent: Torrent) -> None:
    settings = get_settings()
    try:
        ensure_index()
        with httpx.Client(timeout=8) as client:
            response = client.post(
                f"{settings.meili_url}/indexes/{settings.meili_index}/documents",
                headers=_headers(),
                json=[torrent_document(torrent)],
            )
            response.raise_for_status()
    except Exception:
        # Search has a PostgreSQL fallback, so indexing failure must not lose ingestion.
        return


def search_torrents(query: str, limit: int = 25, fmt: str | None = None) -> list[dict] | None:
    settings = get_settings()
    body: dict[str, object] = {"q": query, "limit": limit}
    if fmt:
        safe_fmt = fmt.lower().replace('"', "")
        body["filter"] = f'formats = "{safe_fmt}"'
    try:
        with httpx.Client(timeout=5) as client:
            response = client.post(
                f"{settings.meili_url}/indexes/{settings.meili_index}/search",
                headers=_headers(),
                json=body,
            )
            response.raise_for_status()
            return response.json().get("hits", [])
    except Exception:
        return None
