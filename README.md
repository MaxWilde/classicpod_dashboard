# ClassicPod Dashboard

Web app for browsing music on an iPod Classic (or other libgpod-compatible iPods) from a mounted filesystem path.

The backend shells out to `gpod-ls -M <mountpoint>` from `gpod-utils`, parses its JSON output, and serves a searchable dashboard UI.

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

1. Mount your iPod on the host.
2. Start the app (recommended):

```bash
sudo docker compose down
sudo env IPOD_MOUNTPOINT=/media docker compose up --build -d
```

To set a custom host IP/port:

```bash
sudo env IPOD_MOUNTPOINT=/media HOST_IP=0.0.0.0 HOST_PORT=8090 APP_PORT=8080 UID=$(id -u) GID=$(id -g) docker compose up --build -d
```

3. Open `http://localhost:<HOST_PORT>` (default `8080`).
4. Keep mountpoint input as `/ipod` (default in container), then click `Load Library`.

Hot unplug/replug support:

- The backend only uses the exact mountpoint you specify (default `/ipod` in the container).
- It may probe descendant directories under that same mountpoint to find `iTunesDB` (for example `/ipod/IPOD`, `/ipod/IPOD1`).
- On unplug/replug it retries for a short window on that same path before returning an error.
- Retry window is configurable with `IPOD_RECONNECT_WAIT_SECONDS` (default `60` in docker-compose).
- Per-attempt `gpod-ls` timeout is configurable with `GPOD_LS_ATTEMPT_TIMEOUT_SECONDS` (default `8` in docker-compose).
- Host exposure is configurable with `HOST_IP` and `HOST_PORT`.
- App bind is configurable with `APP_HOST` and `APP_PORT`.
- Gunicorn runtime is configurable with `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, and `GUNICORN_GRACEFUL_TIMEOUT`.
- Use a stable parent bind (e.g. `IPOD_MOUNTPOINT=/media`) so replugged iPods that remount as `IPOD1` still work without compose changes.
- If you use `sudo`, pass env vars through sudo (`sudo env IPOD_MOUNTPOINT=/media UID=$(id -u) GID=$(id -g) docker compose up --build`) so compose does not fall back to defaults.

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
