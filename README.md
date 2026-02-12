# ClassicPod Dashboard

Web app for browsing music on an iPod Classic (or other libgpod-compatible iPods) from a mounted filesystem path.

The backend shells out to `gpod-ls -M <mountpoint>` from [`gpod-utils`](https://github.com/MaxWilde/gpod-utils), parses its JSON output, and serves a searchable dashboard UI.

## Features

- Load an iPod library from a mountpoint or direct `iTunesDB` path
- Display album artwork (embedded tags or folder cover files when available)
- Track summary metrics (tracks, artists, albums, total time)
- Search and sort by title/artist/album/year/duration
- Click any album card to open a popout with full album tracks and metadata
- Add music from local files or entire folders via upload
- FLAC files are automatically converted to ALAC (.m4a) with iPod-friendly MP4 tags and embedded cover art
- Hover-to-delete controls for tracks and full albums (using `gpod-rm`)
- Docker image based on Debian Bookworm that builds `gpod-utils` directly from GitHub

## Run with Docker

1. Mount your iPod on the host (example: `/run/media/max/IPOD`).
2. Start the app:

```bash
UID=$(id -u) GID=$(id -g) IPOD_MOUNTPOINT=/run/media/max/IPOD docker compose up --build
```

3. Open `http://localhost:8080`.
4. Keep mountpoint input as `/ipod` (default in container), then click `Load Library`.

Hot unplug/replug support:

- The backend auto-discovers iPod mounts under `IPOD_DISCOVERY_ROOTS` (defaults include `/run-media-host`, `/media-host`, `/mnt-host`, and `/ipod`).
- `docker-compose.yml` mounts `/run/media`, `/media`, and `/mnt` into the container with `rshared` propagation.
- After unplug/replug, click `Load Library` again; container recreation is not required.

Troubleshooting write access in Docker:

```bash
docker compose exec classicpod-dashboard sh -lc 'id && ls -ld /ipod && touch /ipod/.classicpod_write_test && rm /ipod/.classicpod_write_test'
```

If that `touch` fails, Docker cannot write to the mounted iPod path (permissions or mount-type issue), so add/delete will fail even if `gpod-cp`/`gpod-rm` work directly on host.

## Run locally (without Docker)

Install dependencies:

```bash
pip install -r requirements.txt
```

You must also have `gpod-ls` installed and available in `PATH` (via `gpod-utils`).
If you use FLAC -> ALAC conversion, `ffmpeg` must also be installed.

Run:

```bash
python app.py
```

Open `http://localhost:8080` and provide your host mountpoint path.

## API

`GET /api/library?mountpoint=/path/to/ipod`

Response includes:

- `track_count`, `artist_count`, `album_count`
- `total_duration_seconds`
- `playlists`
- `tracks`

`GET /api/cover?mountpoint=/path/to/ipod&ipod_path=/iPod_Control/Music/F00/ABCD.mp3`

- Returns album-art image bytes for a track when available

`POST /api/delete-tracks`

- JSON body:
  - `mountpoint` (string)
  - `ipod_paths` (array of iPod file paths)
- Deletes tracks from the iPod via `gpod-rm`

`POST /api/add-tracks`

- `multipart/form-data` fields:
  - `mountpoint` (string)
  - `files` (one or more uploaded audio files)
- Adds tracks to the iPod via `gpod-cp`
- FLAC uploads are automatically converted to ALAC before copy
- Conversion uses larger ffmpeg probe/analyze windows and a FLAC-demuxer fallback for hard-to-detect FLAC files
- Non-audio files in folder uploads are skipped automatically
