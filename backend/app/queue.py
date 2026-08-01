import hashlib
import json
import uuid
from datetime import datetime, timezone

from redis import Redis

from .config import get_settings

QUEUE_KEY = "stl-sniffer:metadata"
JOB_PREFIX = "stl-sniffer:job:"
JOB_TTL_SECONDS = 86400


def redis_client() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def enqueue(payload: dict) -> str:
    client = redis_client()
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    job_key = f"{JOB_PREFIX}{job_id}"
    client.hset(
        job_key,
        mapping={
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "message": "Queued for metadata inspection",
        },
    )
    client.expire(job_key, JOB_TTL_SECONDS)
    client.rpush(QUEUE_KEY, json.dumps({"job_id": job_id, **payload}))
    return job_id


def update_job(job_id: str, **fields: object) -> None:
    client = redis_client()
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    clean = {key: str(value) for key, value in fields.items() if value is not None}
    job_key = f"{JOB_PREFIX}{job_id}"
    client.hset(job_key, mapping=clean)
    client.expire(job_key, JOB_TTL_SECONDS)


def get_job(job_id: str) -> dict | None:
    data = redis_client().hgetall(f"{JOB_PREFIX}{job_id}")
    return data or None


def submission_allowed(client_ip: str) -> tuple[bool, int]:
    settings = get_settings()
    limit = max(1, settings.submission_rate_limit_per_hour)
    digest = hashlib.sha256(client_ip.encode("utf-8", errors="ignore")).hexdigest()[:24]
    key = f"stl-sniffer:rate:{digest}"
    client = redis_client()
    count = client.incr(key)
    if count == 1:
        client.expire(key, 3600)
    return count <= limit, limit
