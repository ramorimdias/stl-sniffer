# STL Sniffer

STL Sniffer is a metadata-first search index for BitTorrent swarms that contain 3D-model files such as STL, 3MF, STEP, OBJ, IGES and OpenSCAD.

The v1 service accepts a magnet URI or `.torrent` metainfo file, resolves/reads the torrent metadata, rejects torrents that contain no configured model extensions, stores the searchable metadata in PostgreSQL, and indexes it in Meilisearch. It does **not** download or host the model payload.

## v1 scope

- Submit magnet URIs.
- Upload `.torrent` metainfo files.
- Resolve magnet metadata with aria2 / BitTorrent metadata exchange.
- Inspect file paths without downloading model payloads.
- Detect STL, 3MF, OBJ, STEP, STP, IGES, IGS and SCAD by default.
- Deduplicate by BitTorrent v1/v2 info hash when available.
- Store torrent and file metadata in PostgreSQL.
- Full-text search through Meilisearch with PostgreSQL fallback.
- Minimal responsive web UI.
- Provenance/source URL field.
- Optional approved RSS/Atom source polling for automatic discovery.
- Job status polling for asynchronous metadata resolution.
- Redis-backed submission rate limiting.
- Traefik-ready Docker Compose deployment.
- Optional Torrust Tracker profile for running your own HTTP/UDP tracker alongside the index.

Not in v1: DHT-wide harvesting, payload storage, previews/rendering, user accounts, comments, ratings, arbitrary website crawling, or moderation workflows.

## Stack

- FastAPI + Jinja UI
- PostgreSQL 17
- Redis 7
- Meilisearch
- aria2 metadata resolver
- Docker Compose
- Traefik integration through `aio_network`
- Optional Torrust Tracker

## Deploy on the existing VPS

The intended path is:

```bash
/opt/docker/apps/stl-sniffer
```

Copy the repository there, then:

```bash
cd /opt/docker/apps/stl-sniffer
cp .env.example .env
nano .env
```

At minimum, set strong values for:

```env
STL_SNIFFER_HOSTNAME=stlsniffer.your-domain.example
POSTGRES_PASSWORD=...
DATABASE_URL=postgresql+psycopg://stl_sniffer:THE_SAME_PASSWORD@postgres:5432/stl_sniffer
MEILI_MASTER_KEY=...
```

Make sure the existing Traefik network is available:

```bash
docker network inspect aio_network >/dev/null
```

Then start:

```bash
docker compose config
docker compose up -d --build
```

Check:

```bash
docker compose ps
docker compose logs --tail=150 api worker
curl -fsS https://stlsniffer.your-domain.example/health
```

Expected health response:

```json
{"status":"ok","service":"stl-sniffer"}
```

## Optional approved-source discovery

For automatic discovery, configure one or more RSS/Atom feeds that you are permitted to poll. The poller extracts magnet links from feed items and submits them through the same metadata-only worker.

```env
SOURCE_FEEDS=https://example.org/models.rss,https://example.net/releases.atom
SOURCE_POLL_INTERVAL_SECONDS=900
```

Start the crawler profile alongside the core stack:

```bash
docker compose --profile crawler up -d
```

The poller remembers seen magnets in Redis for 30 days. It does not crawl arbitrary pages and does not download torrent payloads.

## Optional BitTorrent tracker

The index and tracker are intentionally separate. STL Sniffer can index external torrents without operating their tracker. If you also want to run a conventional tracker, v1 includes Torrust behind the `tracker` Compose profile. Torrust provides HTTP and UDP tracker services and collects swarm statistics.

The public Torrust Tracker Docker tags are currently amd64-only, while this project targets an Oracle Ampere ARM64 host. The optional profile therefore builds Torrust's `develop` branch from its official `Containerfile` on the host architecture instead of pulling the prebuilt tracker image. This makes the tracker build substantially heavier than the core STL Sniffer stack.

Before enabling it, replace the tracker admin token in `.env` and make sure the selected ports are allowed by your host firewall / Oracle Cloud network rules:

```env
TORRUST_TRACKER_ADMIN_TOKEN=replace-with-a-long-random-secret
TRACKER_HTTP_PORT=7070
TRACKER_UDP_PORT=6969
```

Start the profile:

```bash
docker compose --profile tracker up -d
```

Default announce endpoints are then:

```text
http://YOUR_SERVER:7070/announce
udp://YOUR_SERVER:6969/announce
```

The tracker does not decide whether a torrent contains STL files. That classification remains the responsibility of the STL Sniffer metadata worker.

## API

### Submit a magnet

```bash
curl -X POST https://stlsniffer.example.com/api/submissions/magnet \
  -H 'content-type: application/json' \
  -d '{
    "magnet_uri": "magnet:?xt=urn:btih:...",
    "source_url": "https://example.org/original-page"
  }'
```

### Submit a `.torrent`

```bash
curl -X POST https://stlsniffer.example.com/api/submissions/torrent \
  -F 'torrent=@example.torrent' \
  -F 'source_url=https://example.org/original-page'
```

### Check ingestion

```bash
curl https://stlsniffer.example.com/api/jobs/JOB_ID
```

### Search

```bash
curl 'https://stlsniffer.example.com/api/torrents?q=voron&format=stl'
```

## Ingestion behavior

For magnet submissions the worker invokes aria2 in metadata-only mode. The generated `.torrent` metainfo is parsed and then removed. Payload files are not requested by STL Sniffer.

The worker indexes a torrent only when at least one file extension matches `MODEL_EXTENSIONS`.

Configure extensions in `.env`:

```env
MODEL_EXTENSIONS=.stl,.3mf,.obj,.step,.stp,.iges,.igs,.scad
```

## Operational and legal boundary

STL Sniffer is an index of metadata and peer-discovery references. Operators should only ingest sources they are authorized to index, preserve provenance where possible, publish clear acceptable-use and takedown/reporting procedures before opening public submissions, and comply with applicable law. The v1 intentionally does not perform indiscriminate DHT harvesting.

## Next milestones

1. Admin moderation queue and report/takedown workflow.
2. Source adapters for explicitly approved RSS/API/sitemap sources.
3. License and creator metadata normalization.
4. Background swarm availability checks.
5. Integrate tracker scrape/statistics into indexed torrent pages.
6. Model preview generation only for content the operator is authorized to retrieve.
7. Accounts, saved searches and notifications.
