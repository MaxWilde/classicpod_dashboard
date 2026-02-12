from __future__ import annotations

import json
import errno
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from flac2alac_converter import Flac2AlacError
from flac2alac_converter import convert_flac_to_alac

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


class GpodError(RuntimeError):
    pass


def _looks_like_missing_itunesdb(message: str) -> bool:
    lowered = message.lower()
    return (
        "couldn't find an ipod database" in lowered
        or "failed to parse itunesdb" in lowered
        or "failed to prase itunesdb" in lowered
    )


def _reconnect_wait_seconds(default_seconds: float = 45.0) -> float:
    raw = os.environ.get("IPOD_RECONNECT_WAIT_SECONDS", "").strip()
    if not raw:
        return default_seconds
    try:
        parsed = float(raw)
    except ValueError:
        return default_seconds
    return max(2.0, min(parsed, 180.0))


def _itunesdb_paths_for_root(root: str) -> tuple[str, str, str]:
    return (
        os.path.join(root, "iPod_Control", "iTunes", "iTunesDB"),
        os.path.join(root, "iTunes_Control", "iTunes", "iTunesDB"),
        os.path.join(root, "iPod_Control", "iTunesDB"),
    )


def _has_itunesdb_under_root(root: str) -> bool:
    return any(os.path.isfile(path) for path in _itunesdb_paths_for_root(root))


def _discover_ipod_roots_within(base: str, max_depth: int = 2) -> list[str]:
    if not os.path.isdir(base):
        return []

    roots: list[str] = []
    stack: list[tuple[str, int]] = [(base, 0)]

    while stack:
        current, depth = stack.pop()

        if _has_itunesdb_under_root(current):
            roots.append(current)
            continue
        if depth >= max_depth:
            continue

        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, depth + 1))
        except OSError:
            continue

    return roots


def _gpod_ls_candidates(requested_mountpoint: str) -> list[tuple[str, str]]:
    mountpoint = os.path.abspath(requested_mountpoint)
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(ls_arg: str, effective_mountpoint: str) -> None:
        normalized_arg = os.path.abspath(ls_arg)
        normalized_mountpoint = os.path.abspath(effective_mountpoint)
        key = (normalized_arg, normalized_mountpoint)
        if key in seen:
            return
        seen.add(key)
        candidates.append(key)

    add(mountpoint, mountpoint)

    if os.path.isfile(mountpoint) and os.path.basename(mountpoint).lower() == "itunesdb":
        parent_mount = os.path.dirname(os.path.dirname(mountpoint))
        add(parent_mount, parent_mount)
        return candidates

    if os.path.isdir(mountpoint):
        for db_path in _itunesdb_paths_for_root(mountpoint):
            add(db_path, mountpoint)
        for discovered_root in _discover_ipod_roots_within(mountpoint):
            add(discovered_root, discovered_root)
            for db_path in _itunesdb_paths_for_root(discovered_root):
                add(db_path, discovered_root)

    return candidates


def _run_gpod_ls_once(mountpoint: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gpod-ls", "-M", mountpoint],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


def _gpod_ls_attempt_timeout(total_timeout_seconds: int) -> int:
    raw = os.environ.get("GPOD_LS_ATTEMPT_TIMEOUT_SECONDS", "").strip()
    if raw:
        try:
            parsed = int(raw)
            return max(2, min(parsed, 30))
        except ValueError:
            pass
    return max(2, min(10, total_timeout_seconds))


def _run_gpod_ls_with_recovery(
    requested_mountpoint: str, timeout_seconds: int
) -> tuple[str, subprocess.CompletedProcess[str]]:
    mountpoint = os.path.abspath(requested_mountpoint)
    recovery_window = _reconnect_wait_seconds()
    deadline = time.monotonic() + recovery_window

    while True:
        saw_any_path = False
        last_missing_db_result: tuple[str, subprocess.CompletedProcess[str]] | None = None
        attempt_timeout = _gpod_ls_attempt_timeout(timeout_seconds)

        for ls_arg, effective_mountpoint in _gpod_ls_candidates(mountpoint):
            if not os.path.exists(ls_arg):
                continue
            saw_any_path = True
            try:
                result = _run_gpod_ls_once(ls_arg, attempt_timeout)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise GpodError(
                        f"Timed out reading iTunesDB at {requested_mountpoint}. "
                        "Device may still be settling after reconnect."
                    )
                continue
            if result.returncode == 0:
                return effective_mountpoint, result

            message = result.stderr.strip() or result.stdout.strip() or "Unknown gpod-ls error."
            if _looks_like_missing_itunesdb(message):
                last_missing_db_result = (effective_mountpoint, result)
                continue
            return effective_mountpoint, result

        if time.monotonic() >= deadline:
            if last_missing_db_result is not None:
                return last_missing_db_result
            if saw_any_path:
                raise GpodError(
                    f"Mountpoint is present but iTunesDB is not readable yet: {requested_mountpoint}."
                )
            raise GpodError(
                f"Mountpoint became unavailable: {requested_mountpoint}. "
                "Reconnect/remount the device at the same path and try again."
            )
        time.sleep(0.5)



def load_library(mountpoint: str, timeout_seconds: int = 120) -> dict[str, Any]:
    if not mountpoint:
        raise GpodError("Mountpoint cannot be empty.")
    try:
        resolved_mountpoint, result = _run_gpod_ls_with_recovery(mountpoint, timeout_seconds)
    except FileNotFoundError as exc:
        raise GpodError(
            "gpod-ls is not installed or not available in PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GpodError("Timed out while reading the iPod database.") from exc

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown gpod-ls error."
        if _looks_like_missing_itunesdb(message):
            raise GpodError(
                "gpod-ls could not find a readable iTunesDB yet. "
                "If you just replugged the iPod, wait a few seconds and try again."
            )
        raise GpodError(f"gpod-ls failed: {message}")

    data = parse_gpod_output(result.stdout)
    ipod_data = data.get("ipod_data", {})
    device = ipod_data.get("device", {}) if isinstance(ipod_data.get("device", {}), dict) else {}
    playlists = ipod_data.get("playlists", {}).get("items", [])
    master = _find_master_playlist(playlists)
    tracks = master.get("tracks", [])

    normalized_tracks = [_normalize_track(track) for track in tracks]
    artists = {t["artist"] for t in normalized_tracks if t["artist"]}
    albums = {t["album"] for t in normalized_tracks if t["album"]}
    total_duration_seconds = sum(t["duration_seconds"] for t in normalized_tracks)
    generation = str(device.get("generation") or "").strip()

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "mountpoint": resolved_mountpoint,
        "device": {
            "generation": generation,
            "model_name": str(device.get("model_name") or ""),
            "model_number": str(device.get("model_number") or ""),
        },
        "track_count": len(normalized_tracks),
        "artist_count": len(artists),
        "album_count": len(albums),
        "total_duration_seconds": round(total_duration_seconds, 2),
        "playlists": [
            {
                "name": item.get("name") or "Unknown",
                "type": item.get("type") or "playlist",
                "count": item.get("count") or 0,
                "smartpl": bool(item.get("smartpl", False)),
            }
            for item in playlists
        ],
        "tracks": normalized_tracks,
    }


def delete_tracks(mountpoint: str, ipod_paths: Iterable[str], timeout_seconds: int = 180) -> dict[str, Any]:
    if not mountpoint:
        raise GpodError("Mountpoint cannot be empty.")
    if not os.path.exists(mountpoint):
        raise GpodError(f"Mountpoint does not exist: {mountpoint}")

    rm_targets = _normalize_rm_targets(ipod_paths)
    if not rm_targets:
        raise GpodError("No valid file paths or iPod IDs were provided for deletion.")

    total_requested = len(rm_targets)
    all_stdout: list[str] = []
    all_stderr: list[str] = []

    try:
        result = _run_gpod_rm(mountpoint, rm_targets, timeout_seconds)
    except FileNotFoundError as exc:
        raise GpodError("gpod-rm is not installed or not available in PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GpodError("Timed out while deleting tracks from iPod.") from exc
    except OSError as exc:
        if exc.errno == errno.E2BIG:
            raise GpodError("Too many delete targets in one request. Delete fewer tracks at a time.") from exc
        raise GpodError(f"gpod-rm failed: {exc}") from exc

    if result.stdout:
        all_stdout.append(result.stdout.strip())
    if result.stderr:
        all_stderr.append(result.stderr.strip())

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown gpod-rm error."
        raise GpodError(f"gpod-rm failed: {message}")

    return {
        "requested_count": total_requested,
        "stdout": "\n".join([line for line in all_stdout if line]),
        "stderr": "\n".join([line for line in all_stderr if line]),
    }


def add_tracks(
    mountpoint: str,
    file_paths: Iterable[str],
    convert_to_alac: bool = False,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    if not mountpoint:
        raise GpodError("Mountpoint cannot be empty.")
    if not os.path.exists(mountpoint):
        raise GpodError(f"Mountpoint does not exist: {mountpoint}")

    source_files = _normalize_local_paths(file_paths)
    if not source_files:
        raise GpodError("No valid local files were provided.")

    converted_count = 0
    conversion_failures: list[dict[str, str]] = []
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    with tempfile.TemporaryDirectory(prefix="classicpod_add_") as temp_dir:
        # Phase 1: convert all FLAC sources first (if enabled).
        converted_map: dict[str, str] = {}
        if convert_to_alac:
            for index, src in enumerate(source_files):
                if not src.lower().endswith(".flac"):
                    continue
                base_name = os.path.splitext(os.path.basename(src))[0]
                dst = os.path.join(temp_dir, f"{base_name}_{index}.m4a")
                try:
                    _convert_flac_to_alac(src, dst, timeout_seconds=timeout_seconds)
                    converted_map[src] = dst
                    converted_count += 1
                except GpodError as exc:
                    # Fall back to original FLAC for phase 2 copy.
                    conversion_failures.append(
                        {
                            "source": os.path.basename(src),
                            "error": str(exc),
                        }
                    )

        # Phase 2: copy all prepared files to iPod.
        prepared_files = [converted_map.get(src, src) for src in source_files]

        try:
            result = _run_gpod_cp(mountpoint, prepared_files, timeout_seconds)
            if result.stdout:
                all_stdout.append(result.stdout.strip())
            if result.stderr:
                all_stderr.append(result.stderr.strip())

            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "Unknown gpod-cp error."
                raise GpodError(f"gpod-cp failed: {message}")
        except FileNotFoundError as exc:
            raise GpodError("gpod-cp is not installed or not available in PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise GpodError("Timed out while copying tracks to iPod.") from exc
        except OSError as exc:
            if exc.errno == errno.E2BIG:
                raise GpodError("Too many files in one add request. Add fewer files at a time.") from exc
            raise GpodError(f"gpod-cp failed: {exc}") from exc

    return {
        "requested_count": len(source_files),
        "converted_count": converted_count,
        "conversion_failed_count": len(conversion_failures),
        "conversion_failures": conversion_failures,
        "stdout": "\n".join([line for line in all_stdout if line]),
        "stderr": "\n".join([line for line in all_stderr if line]),
    }


def parse_gpod_output(stdout: str) -> dict[str, Any]:
    json_blob = _extract_json_blob(stdout)
    try:
        return json.loads(json_blob)
    except json.JSONDecodeError as exc:
        raise GpodError("Could not parse JSON output from gpod-ls.") from exc


def _extract_json_blob(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise GpodError("No JSON object found in gpod-ls output.")
    return text[start : end + 1]


def _find_master_playlist(playlists: list[dict[str, Any]]) -> dict[str, Any]:
    for playlist in playlists:
        if playlist.get("type") == "master":
            return playlist
    for playlist in playlists:
        if playlist.get("name") == "iPod":
            return playlist
    return playlists[0] if playlists else {"tracks": []}


def _normalize_track(track: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = max(float(track.get("tracklen", 0)) / 1000.0, 0.0)
    return {
        "id": track.get("id"),
        "title": track.get("title") or "Unknown Title",
        "artist": track.get("artist") or "Unknown Artist",
        "album": track.get("album") or "Unknown Album",
        "genre": track.get("genre") or "",
        "year": int(track.get("year") or 0),
        "playcount": int(track.get("playcount") or 0),
        "bitrate": int(track.get("bitrate") or 0),
        "size_bytes": int(track.get("size") or 0),
        "duration_seconds": round(duration_seconds, 2),
        "ipod_path": track.get("ipod_path") or "",
        "artwork": bool(track.get("artwork", False)),
        "checksum": track.get("checksum"),
    }


def _normalize_local_paths(file_paths: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in file_paths:
        if not isinstance(raw, str):
            continue
        path = os.path.abspath(raw.strip())
        if not path or not os.path.isfile(path):
            continue
        if path in seen:
            continue
        seen.add(path)
        cleaned.append(path)
    return cleaned


def _normalize_rm_targets(targets: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in targets:
        if not isinstance(raw, str):
            continue
        token = raw.strip()
        if not token:
            continue
        if token.isdigit():
            if token in seen:
                continue
            seen.add(token)
            cleaned.append(token)
            continue

        raw_parts = [part for part in token.replace("\\", "/").split("/") if part]
        if any(part == ".." for part in raw_parts):
            continue
        if not token.startswith("/"):
            token = "/" + token
        normalized = os.path.normpath(token.replace("\\", "/"))
        if normalized == "/":
            continue
        if normalized.startswith("/.."):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _convert_flac_to_alac(src_path: str, dst_path: str, timeout_seconds: int = 600) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise GpodError("Missing ffmpeg/ffprobe. Install ffmpeg in the container.")

    try:
        convert_flac_to_alac(Path(src_path), Path(dst_path), timeout_seconds=timeout_seconds)
    except Flac2AlacError as exc:
        raise GpodError(f"ALAC conversion failed for {os.path.basename(src_path)}: {exc}") from exc


@contextmanager
def _itunesdb_write_lock(mountpoint: str, timeout_seconds: int) -> Iterable[None]:
    if fcntl is None:
        yield
        return

    normalized = os.path.abspath(mountpoint)
    digest = hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    lock_path = os.path.join(tempfile.gettempdir(), f"classicpod_itunesdb_{digest}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    deadline = time.monotonic() + max(timeout_seconds, 1)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise GpodError("Timed out waiting for iTunesDB write lock.")
                time.sleep(0.1)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _run_gpod_cp(mountpoint: str, sources: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    command = ["gpod-cp", "-M", mountpoint, *sources]
    with _itunesdb_write_lock(mountpoint, timeout_seconds):
        return subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )


def _run_gpod_rm(mountpoint: str, targets: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    command = ["gpod-rm", "-M", mountpoint, *targets]
    try:
        with _itunesdb_write_lock(mountpoint, timeout_seconds):
            return subprocess.run(
                command,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
    except OSError as exc:
        if exc.errno != errno.E2BIG:
            raise
        # Fallback: invoke one path at a time if argv is too large.
        merged = subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        with _itunesdb_write_lock(mountpoint, timeout_seconds):
            for target in targets:
                res = subprocess.run(
                    ["gpod-rm", "-M", mountpoint, target],
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                )
                merged = subprocess.CompletedProcess(
                    args=command,
                    returncode=res.returncode if res.returncode != 0 else merged.returncode,
                    stdout=(merged.stdout + ("\n" if merged.stdout and res.stdout else "") + (res.stdout or "")),
                    stderr=(merged.stderr + ("\n" if merged.stderr and res.stderr else "") + (res.stderr or "")),
                )
                if res.returncode != 0:
                    break
        return merged
