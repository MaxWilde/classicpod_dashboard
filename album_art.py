from __future__ import annotations

import imghdr
import os
from functools import lru_cache


class CoverArtError(RuntimeError):
    pass


def resolve_mount_root(mountpoint: str) -> str:
    abs_mount = os.path.abspath(mountpoint)
    if os.path.isdir(abs_mount):
        return abs_mount

    if os.path.isfile(abs_mount):
        marker = f"{os.sep}iPod_Control{os.sep}"
        if marker in abs_mount:
            return abs_mount.split(marker, 1)[0]
        return os.path.dirname(abs_mount)

    raise CoverArtError(f"Mountpoint does not exist: {mountpoint}")


def resolve_track_abspath(mountpoint: str, ipod_path: str) -> str:
    if not ipod_path:
        raise CoverArtError("Track path is required.")

    raw_parts = [part for part in ipod_path.replace("\\", "/").split("/") if part]
    if any(part == ".." for part in raw_parts):
        raise CoverArtError("Invalid track path.")

    cleaned = os.path.normpath("/" + ipod_path.lstrip("/"))
    if cleaned.startswith("/.."):
        raise CoverArtError("Invalid track path.")

    mount_root = resolve_mount_root(mountpoint)
    abs_track = os.path.abspath(os.path.join(mount_root, cleaned.lstrip("/")))
    if os.path.commonpath([mount_root, abs_track]) != mount_root:
        raise CoverArtError("Track path escapes mountpoint.")
    return abs_track


def load_cover(mountpoint: str, ipod_path: str) -> tuple[bytes, str]:
    abs_track = resolve_track_abspath(mountpoint, ipod_path)
    if not os.path.isfile(abs_track):
        raise CoverArtError(f"Track file not found: {ipod_path}")

    mtime_ns = os.stat(abs_track).st_mtime_ns
    return _load_cover_cached(abs_track, mtime_ns)


@lru_cache(maxsize=4096)
def _load_cover_cached(abs_track: str, mtime_ns: int) -> tuple[bytes, str]:
    _ = mtime_ns
    data = _extract_embedded_cover(abs_track)
    if data is None:
        folder_image = _try_folder_cover(abs_track)
        if folder_image is None:
            raise CoverArtError("No album art found for track.")
        data, mime = folder_image
        return data, mime

    bytes_data, mime = data
    return bytes_data, mime


def _extract_embedded_cover(path: str) -> tuple[bytes, str] | None:
    try:
        from mutagen import File as MutagenFile
        from mutagen.flac import FLAC
        from mutagen.id3 import APIC
        from mutagen.mp4 import MP4, MP4Cover
    except ImportError:
        return None

    audio = MutagenFile(path)
    if audio is None:
        return None

    if isinstance(audio, MP4):
        covers = audio.tags.get("covr", []) if audio.tags else []
        if covers:
            first = covers[0]
            if isinstance(first, MP4Cover):
                fmt = first.imageformat
                if fmt == MP4Cover.FORMAT_PNG:
                    return bytes(first), "image/png"
                return bytes(first), "image/jpeg"
            guessed = _guess_mime(bytes(first))
            return bytes(first), guessed

    if isinstance(audio, FLAC):
        if audio.pictures:
            pic = audio.pictures[0]
            mime = pic.mime or _guess_mime(pic.data)
            return pic.data, mime

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    if hasattr(tags, "getall"):
        apic_tags = tags.getall("APIC")
        if apic_tags:
            tag = apic_tags[0]
            if isinstance(tag, APIC):
                mime = tag.mime or _guess_mime(tag.data)
                return tag.data, mime

    if "metadata_block_picture" in tags:
        pictures = tags.get("metadata_block_picture")
        if pictures:
            # Not all mutagen types decode this uniformly; fallback to folder cover.
            return None

    return None


def _try_folder_cover(track_path: str) -> tuple[bytes, str] | None:
    folder = os.path.dirname(track_path)
    candidates = (
        "cover.jpg",
        "cover.jpeg",
        "cover.png",
        "folder.jpg",
        "folder.jpeg",
        "folder.png",
        "album.jpg",
        "album.png",
    )
    for name in candidates:
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        with open(full, "rb") as fh:
            data = fh.read()
        return data, _guess_mime(data)
    return None


def _guess_mime(data: bytes) -> str:
    img_type = imghdr.what(None, h=data)
    if img_type == "png":
        return "image/png"
    if img_type in {"jpg", "jpeg"}:
        return "image/jpeg"
    if img_type == "gif":
        return "image/gif"
    return "application/octet-stream"
