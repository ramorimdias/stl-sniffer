from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import get_settings


def resolve_magnet(magnet_uri: str) -> Path:
    settings = get_settings()
    workdir = Path(tempfile.mkdtemp(prefix="magnet-", dir=settings.tmp_dir))
    command = [
        "aria2c",
        "--bt-metadata-only=true",
        "--bt-save-metadata=true",
        "--follow-torrent=mem",
        "--seed-time=0",
        "--file-allocation=none",
        "--summary-interval=0",
        "--console-log-level=warn",
        "--enable-dht=true",
        "--enable-dht6=false",
        f"--dir={workdir}",
        magnet_uri,
    ]

    try:
        try:
            subprocess.run(
                command,
                cwd=workdir,
                check=False,
                timeout=settings.magnet_resolve_timeout_seconds,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except subprocess.TimeoutExpired:
            # aria2 can have written the metainfo before the process times out.
            pass

        torrent_files = sorted(workdir.glob("*.torrent"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not torrent_files:
            raise RuntimeError("Could not resolve torrent metadata from peers within the configured timeout")

        destination = settings.inbox_dir / f"resolved-{torrent_files[0].name}"
        shutil.copy2(torrent_files[0], destination)
        return destination
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
