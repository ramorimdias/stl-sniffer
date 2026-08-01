from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select

from .db import SessionLocal, init_db
from .metadata_resolver import resolve_magnet
from .models import Torrent, TorrentFile
from .queue import QUEUE_KEY, redis_client, update_job
from .search import index_torrent
from .torrent_parser import parse_magnet_hashes, parse_torrent_file


def _find_existing(session, v1: str | None, v2: str | None) -> Torrent | None:
    conditions = []
    if v1:
        conditions.append(Torrent.info_hash_v1 == v1)
    if v2:
        conditions.append(Torrent.info_hash_v2 == v2)
    if not conditions:
        return None
    return session.scalar(select(Torrent).where(or_(*conditions)).limit(1))


def process_job(payload: dict) -> None:
    job_id = payload["job_id"]
    kind = payload.get("kind")
    update_job(job_id, status="processing", message="Resolving and inspecting torrent metadata")

    resolved_path: Path | None = None
    try:
        if kind == "magnet":
            magnet_uri = payload["magnet_uri"]
            resolved_path = resolve_magnet(magnet_uri)
            parsed = parse_torrent_file(resolved_path)
            magnet_v1, magnet_v2 = parse_magnet_hashes(magnet_uri)
            if magnet_v1:
                parsed.info_hash_v1 = magnet_v1
            if magnet_v2:
                parsed.info_hash_v2 = magnet_v2
            metadata_source = "magnet"
        elif kind == "torrent":
            resolved_path = Path(payload["path"])
            parsed = parse_torrent_file(resolved_path)
            magnet_uri = None
            metadata_source = "torrent_upload"
        else:
            raise ValueError("Unknown ingestion job type")

        if parsed.model_file_count == 0:
            update_job(
                job_id,
                status="rejected",
                message="Torrent metadata contains no configured 3D-model file extensions",
            )
            return

        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            torrent = _find_existing(session, parsed.info_hash_v1, parsed.info_hash_v2)
            if torrent:
                torrent.last_seen_at = now
                if payload.get("source_url") and not torrent.source_url:
                    torrent.source_url = payload["source_url"]
                if magnet_uri and not torrent.magnet_uri:
                    torrent.magnet_uri = magnet_uri
                session.commit()
                session.refresh(torrent)
            else:
                torrent = Torrent(
                    info_hash_v1=parsed.info_hash_v1,
                    info_hash_v2=parsed.info_hash_v2,
                    name=parsed.name,
                    total_size=parsed.total_size,
                    file_count=len(parsed.files),
                    model_file_count=parsed.model_file_count,
                    formats=parsed.formats,
                    source_url=payload.get("source_url"),
                    magnet_uri=magnet_uri,
                    metadata_source=metadata_source,
                    status="active",
                    discovered_at=now,
                    last_seen_at=now,
                    files=[
                        TorrentFile(
                            path=item.path,
                            extension=item.extension,
                            size=item.size,
                            is_model=item.is_model,
                        )
                        for item in parsed.files
                    ],
                )
                session.add(torrent)
                session.commit()
                session.refresh(torrent)

            index_torrent(torrent)
            update_job(
                job_id,
                status="done",
                message=f"Indexed {torrent.model_file_count} model files",
                torrent_id=torrent.id,
            )
    except Exception as exc:
        update_job(job_id, status="failed", message=str(exc)[:1000])
    finally:
        if resolved_path and resolved_path.name.startswith(("resolved-", "upload-")):
            resolved_path.unlink(missing_ok=True)


def main() -> None:
    init_db()
    client = redis_client()
    while True:
        item = client.brpop(QUEUE_KEY, timeout=5)
        if not item:
            time.sleep(0.25)
            continue
        _, raw_payload = item
        try:
            process_job(json.loads(raw_payload))
        except Exception:
            time.sleep(1)


if __name__ == "__main__":
    main()
