from __future__ import annotations

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET

import httpx

from .config import get_settings
from .queue import enqueue, redis_client

logger = logging.getLogger("stl-sniffer.source-poller")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>]+", re.IGNORECASE)
SEEN_TTL_SECONDS = 30 * 24 * 3600


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

    # Some simple feeds place magnet links at the document root rather than item/entry nodes.
    if not entries:
        entries = [root]

    for entry in entries:
        page_url = _find_http_page(entry) or feed_url
        for magnet in _extract_magnets(entry):
            results.append((magnet, page_url))
    return results


def poll_feed(feed_url: str) -> int:
    client = redis_client()
    headers = {"User-Agent": "STL-Sniffer/0.1 metadata-indexer"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as http:
        response = http.get(feed_url)
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError("Feed exceeds 10 MiB limit")
        pairs = parse_feed(response.text, feed_url)

    queued = 0
    for magnet, source_url in pairs:
        digest = hashlib.sha256(magnet.encode("utf-8", errors="ignore")).hexdigest()
        seen_key = f"stl-sniffer:feed-seen:{digest}"
        if not client.set(seen_key, "1", nx=True, ex=SEEN_TTL_SECONDS):
            continue
        enqueue({"kind": "magnet", "magnet_uri": magnet, "source_url": source_url})
        queued += 1
    return queued


def main() -> None:
    settings = get_settings()
    interval = max(60, settings.source_poll_interval_seconds)
    feeds = settings.source_feed_list
    if not feeds:
        logger.warning("No SOURCE_FEEDS configured; source poller will remain idle")

    while True:
        for feed_url in feeds:
            try:
                queued = poll_feed(feed_url)
                logger.info("feed=%s queued=%s", feed_url, queued)
            except Exception as exc:
                logger.warning("feed=%s error=%s", feed_url, exc)
        time.sleep(interval)


if __name__ == "__main__":
    main()
