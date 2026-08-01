from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

from .bencode import decode_torrent
from .config import get_settings


@dataclass
class ParsedFile:
    path: str
    size: int
    extension: str
    is_model: bool


@dataclass
class ParsedTorrent:
    name: str
    info_hash_v1: str | None
    info_hash_v2: str | None
    total_size: int
    files: list[ParsedFile]
    formats: list[str]

    @property
    def model_file_count(self) -> int:
        return sum(1 for item in self.files if item.is_model)


def decode_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def key_get(mapping: dict, key: bytes, default=None):
    return mapping.get(key, mapping.get(key.decode(), default))


def parse_magnet_hashes(magnet_uri: str) -> tuple[str | None, str | None]:
    params = parse_qs(urlparse(magnet_uri).query)
    v1 = None
    v2 = None
    for xt in params.get("xt", []):
        lowered = xt.lower()
        if lowered.startswith("urn:btih:"):
            candidate = xt.split(":", 2)[2]
            if len(candidate) == 40:
                v1 = candidate.lower()
            elif len(candidate) == 32:
                try:
                    v1 = base64.b32decode(candidate.upper()).hex()
                except Exception:
                    pass
        elif lowered.startswith("urn:btmh:"):
            candidate = xt.split(":", 2)[2]
            # Common multihash representation: 1220 + 64 hex chars for sha2-256.
            if candidate.lower().startswith("1220") and len(candidate) >= 68:
                v2 = candidate[4:68].lower()
    return v1, v2


def _extract_v1_files(info: dict, root_name: str) -> list[tuple[str, int]]:
    files = key_get(info, b"files")
    if isinstance(files, list):
        result: list[tuple[str, int]] = []
        for item in files:
            path_parts = key_get(item, b"path.utf-8") or key_get(item, b"path") or []
            path = "/".join(decode_text(part) for part in path_parts)
            if root_name and path and not path.startswith(f"{root_name}/"):
                path = f"{root_name}/{path}"
            result.append((path or root_name, int(key_get(item, b"length", 0) or 0)))
        return result

    return [(root_name, int(key_get(info, b"length", 0) or 0))]


def _walk_v2_tree(node: dict, prefix: list[str]) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for raw_key, child in node.items():
        key = decode_text(raw_key)
        if key == "":
            path = "/".join(prefix)
            length = int(key_get(child, b"length", 0) or 0) if isinstance(child, dict) else 0
            result.append((path, length))
            continue
        if isinstance(child, dict):
            result.extend(_walk_v2_tree(child, [*prefix, key]))
    return result


def parse_torrent_file(path: Path) -> ParsedTorrent:
    raw = path.read_bytes()
    decoded = decode_torrent(raw)
    meta = decoded.value
    info = key_get(meta, b"info")
    if not isinstance(info, dict):
        raise ValueError("Torrent has no info dictionary")

    root_name = decode_text(key_get(info, b"name.utf-8") or key_get(info, b"name") or "unnamed")

    meta_version = int(key_get(info, b"meta version", 1) or 1)
    info_hash_v1 = hashlib.sha1(decoded.info_bytes).hexdigest() if meta_version != 2 or key_get(info, b"pieces") else None
    info_hash_v2 = hashlib.sha256(decoded.info_bytes).hexdigest() if meta_version == 2 else None

    file_pairs: list[tuple[str, int]] = []
    file_tree = key_get(info, b"file tree")
    if isinstance(file_tree, dict):
        file_pairs = _walk_v2_tree(file_tree, [root_name])
    if not file_pairs:
        file_pairs = _extract_v1_files(info, root_name)

    model_exts = get_settings().model_extension_set
    parsed_files: list[ParsedFile] = []
    formats: set[str] = set()

    for file_path, size in file_pairs:
        suffix = PurePosixPath(file_path).suffix.lower()
        is_model = suffix in model_exts
        if is_model:
            formats.add(suffix.lstrip("."))
        parsed_files.append(
            ParsedFile(path=file_path, size=max(0, int(size)), extension=suffix, is_model=is_model)
        )

    return ParsedTorrent(
        name=root_name,
        info_hash_v1=info_hash_v1,
        info_hash_v2=info_hash_v2,
        total_size=sum(item.size for item in parsed_files),
        files=parsed_files,
        formats=sorted(formats),
    )
