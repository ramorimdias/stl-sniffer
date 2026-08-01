from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from .config import get_settings
from .queue import enqueue, redis_client

logger = logging.getLogger("stl-sniffer.source-poller")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>]+", re.IGNORECASE)
INFOHASH_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")
SEEN_TTL_SECONDS = 30 * 24 * 3600
INTERNET_ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ACADEMIC_TORRENTS_DATABASE = "https://academictorrents.com/database.xml"


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _find_http_page(entry: ET.Element) -> str | None:
    for element in entry.iter():
        tag = _strip_namespace(element.tag)
        candidates = []
        if element.text:
            candidates.append(element.text.strip())
        candidates.extend(str(value).strip() for value in element.attrib.values())
        if tag == "link":
            href = element.attrib.get("href")
            if href:
                candidates.insert(0, href.strip())
        for candidate in candidates:
            if candidate.startswith(("https://", "http://")):
                return candidate
    return None


def _extract_magnets(entry: ET.Element) -> set[str]:
    magnets: set[str] = set()
    for element in entry.iter():
        values = []
        if element.text:
            values.append(element.text)
        if element.tail:
            values.append(element.tail)
        values.extend(str(value) for value in element.attrib.values())
        for value in values:
            magnets.update(match.rstrip(".,);") for match in MAGNET_RE.findall(value))
    return magnets


def parse_feed(xml_text: str, feed_url: str) -> list[tuple[str, str]]:
    root = ET.fromstring(xml_text)
    entries = [node for node in root.iter() if _strip_namespace(node.tag) in {"item", "entry"}]
    results: list[tuple[str, str]] = []
    if not entries:
        entries = [root]
    for entry in entries:
        page_url = _find_http_page(entry) or feed_url
        for magnet in _extract_magnets(entry):
            results.append((magnet, page_url))
    return results


def _queue_magnet(magnet: str, source_url: str, source_key: str) -> bool:
    client = redis_client()
    digest = hashlib.sha256(magnet.encode("utf-8", errors="ignore")).hexdigest()
    seen_key = f"stl-sniffer:source-seen:{source_key}:{digest}"
    if not client.set(seen_key, "1", nx=True, ex=SEEN_TTL_SECONDS):
        return False
    enqueue({"kind": "magnet", "magnet_uri": magnet, "source_url": source_url})
    return True


def poll_feed(feed_url: str) -> int:
    headers = {"User-Agent": "STL-Sniffer/0.2 metadata-indexer"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as http:
        response = http.get(feed_url)
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError("Feed exceeds 10 MiB limit")
        pairs = parse_feed(response.text, feed_url)

    queued = 0
    for magnet, source_url in pairs:
        queued += int(_queue_magnet(magnet, source_url, "feed"))
    return queued


def _ia_torrent_magnet(identifier: str) -> str:
    torrent_url = f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(identifier)}_archive.torrent"
    return f"magnet:?xs={urllib.parse.quote(torrent_url, safe=':/')}"


def poll_internet_archive(query: str, rows: int) -> int:
    params = {
        "q": query,
        "fl[]": ["identifier", "title"],
        "rows": max(1, min(rows, 200)),
        "page": 1,
        "output": "json",
    }
    headers = {"User-Agent": "STL-Sniffer/0.2 metadata-indexer"}
    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as http:
        response = http.get(INTERNET_ARCHIVE_SEARCH, params=params)
        response.raise_for_status()
        data = response.json()

    queued = 0
    for doc in data.get("response", {}).get("docs", []):
        identifier = str(doc.get("identifier") or "").strip()
        if not identifier:
            continue
        source_url = f"https://archive.org/details/{urllib.parse.quote(identifier)}"
        magnet = _ia_torrent_magnet(identifier)
        queued += int(_queue_magnet(magnet, source_url, "internet-archive"))
    return queued


def poll_academic_torrents(limit: int) -> int:
    headers = {"User-Agent": "STL-Sniffer/0.2 metadata-indexer"}
    with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as http:
        response = http.get(ACADEMIC_TORRENTS_DATABASE)
        response.raise_for_status()
        if len(response.content) > 100 * 1024 * 1024:
            raise ValueError("Academic Torrents database exceeds 100 MiB limit")
        root = ET.fromstring(response.content)

    queued = 0
    inspected = 0
    for node in root.iter():
        if inspected >= max(1, limit):
            break
        texts = [node.text or "", node.tail or ""]
        texts.extend(str(value) for value in node.attrib.values())
        blob = " ".join(texts)
        infohash = next(iter(INFOHASH_RE.findall(blob)), None)
        if not infohash:
            continue
        inspected += 1
        magnet = f"magnet:?xt=urn:btih:{infohash.lower()}"
        source_url = "https://academictorrents.com/"
        queued += int(_queue_magnet(magnet, source_url, "academic-torrents"))
    return queued


def main() -> None:
    settings = get_settings()
    interval = max(60, settings.source_poll_interval_seconds)
    feeds = settings.source_feed_list

    while True:
        for feed_url in feeds:
            try:
                queued = poll_feed(feed_url)
                logger.info("source=feed url=%s queued=%s", feed_url, queued)
            except Exception as exc:
                logger.warning("source=feed url=%s error=%s", feed_url, exc)

        if settings.source_enable_internet_archive:
            try:
                queued = poll_internet_archive(
                    settings.source_internet_archive_query,
                    settings.source_internet_archive_rows,
                )
                logger.info("source=internet-archive queued=%s", queued)
            except Exception as exc:
                logger.warning("source=internet-archive error=%s", exc)

        if settings.source_enable_academic_torrents:
            try:
                queued = poll_academic_torrents(settings.source_academic_torrents_limit)
                logger.info("source=academic-torrents queued=%s", queued)
            except Exception as exc:
                logger.warning("source=academic-torrents error=%s", exc)

        if not feeds and not settings.source_enable_internet_archive and not settings.source_enable_academic_torrents:
            logger.warning("No crawler sources enabled; source poller will remain idle")

        time.sleep(interval)


if __name__ == "__main__":
    main()
