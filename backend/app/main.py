from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import HttpUrl, TypeAdapter, ValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import SessionLocal, init_db
from .models import Torrent, TorrentFile
from .queue import enqueue, get_job, submission_allowed
from .schemas import MagnetSubmission
from .search import search_torrents

settings = get_settings()

def _request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_submission_rate_limit(request: Request) -> None:
    allowed, limit = submission_allowed(_request_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Submission limit exceeded ({limit} per hour)")


def _validated_source_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(TypeAdapter(HttpUrl).validate_python(value))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="source_url must be a valid HTTP(S) URL") from exc


app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "stl-sniffer"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.app_name})


@app.get("/submit", response_class=HTMLResponse)
def submit_page(request: Request):
    return templates.TemplateResponse("submit.html", {"request": request, "app_name": settings.app_name})


@app.get("/torrent/{torrent_id}", response_class=HTMLResponse)
def torrent_page(request: Request, torrent_id: int):
    with SessionLocal() as session:
        torrent = session.scalar(
            select(Torrent).options(selectinload(Torrent.files)).where(Torrent.id == torrent_id)
        )
        if not torrent:
            raise HTTPException(status_code=404, detail="Torrent not found")
        model_files = [item for item in torrent.files if item.is_model]
        return templates.TemplateResponse(
            "torrent.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "torrent": torrent,
                "model_files": model_files,
            },
        )


@app.get("/api/stats")
def stats() -> dict:
    with SessionLocal() as session:
        torrent_count = session.scalar(select(func.count(Torrent.id)).where(Torrent.status == "active")) or 0
        model_file_count = session.scalar(
            select(func.count(TorrentFile.id)).where(TorrentFile.is_model.is_(True))
        ) or 0
        return {"torrents": torrent_count, "model_files": model_file_count}


@app.get("/api/torrents")
def list_torrents(
    q: str = Query(default="", max_length=200),
    format: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    if q.strip():
        hits = search_torrents(q.strip(), limit=limit, fmt=format)
        if hits is not None:
            return {"items": hits, "source": "meilisearch"}

    with SessionLocal() as session:
        statement = select(Torrent).where(Torrent.status == "active")
        if q.strip():
            statement = statement.where(Torrent.name.ilike(f"%{q.strip()}%"))
        if format:
            statement = statement.where(Torrent.formats.contains([format.lower()]))
        torrents = session.scalars(statement.order_by(desc(Torrent.discovered_at)).limit(limit)).all()
        return {
            "items": [
                {
                    "id": torrent.id,
                    "name": torrent.name,
                    "formats": torrent.formats,
                    "model_file_count": torrent.model_file_count,
                    "file_count": torrent.file_count,
                    "total_size": torrent.total_size,
                    "source_url": torrent.source_url,
                    "discovered_at": torrent.discovered_at.isoformat(),
                }
                for torrent in torrents
            ],
            "source": "postgres",
        }


@app.get("/api/torrents/{torrent_id}")
def get_torrent(torrent_id: int) -> dict:
    with SessionLocal() as session:
        torrent = session.scalar(
            select(Torrent).options(selectinload(Torrent.files)).where(Torrent.id == torrent_id)
        )
        if not torrent:
            raise HTTPException(status_code=404, detail="Torrent not found")
        return {
            "id": torrent.id,
            "name": torrent.name,
            "info_hash_v1": torrent.info_hash_v1,
            "info_hash_v2": torrent.info_hash_v2,
            "total_size": torrent.total_size,
            "file_count": torrent.file_count,
            "model_file_count": torrent.model_file_count,
            "formats": torrent.formats,
            "source_url": torrent.source_url,
            "magnet_uri": torrent.magnet_uri,
            "metadata_source": torrent.metadata_source,
            "discovered_at": torrent.discovered_at.isoformat(),
            "files": [
                {"path": item.path, "size": item.size, "extension": item.extension, "is_model": item.is_model}
                for item in torrent.files
            ],
        }


@app.post("/api/submissions/magnet", status_code=202)
def submit_magnet(request: Request, submission: MagnetSubmission) -> dict:
    _enforce_submission_rate_limit(request)
    job_id = enqueue(
        {
            "kind": "magnet",
            "magnet_uri": submission.magnet_uri,
            "source_url": str(submission.source_url) if submission.source_url else None,
        }
    )
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/submissions/torrent", status_code=202)
async def submit_torrent_file(
    request: Request,
    torrent: UploadFile = File(...),
    source_url: str | None = Form(default=None),
) -> dict:
    _enforce_submission_rate_limit(request)
    source_url = _validated_source_url(source_url)
    if not torrent.filename or not torrent.filename.lower().endswith(".torrent"):
        raise HTTPException(status_code=400, detail="Only .torrent files are accepted")

    destination = settings.inbox_dir / f"upload-{uuid.uuid4().hex}.torrent"
    written = 0
    try:
        with destination.open("wb") as output:
            while chunk := await torrent.read(1024 * 1024):
                written += len(chunk)
                if written > settings.max_torrent_bytes:
                    raise HTTPException(status_code=413, detail="Torrent metainfo file is too large")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    job_id = enqueue({"kind": "torrent", "path": str(destination), "source_url": source_url})
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return job
