from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


class Flac2AlacError(RuntimeError):
    pass


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

_NUM_PAIR_RE = re.compile(r"^\s*(\d+)\s*(?:/|\sof\s)\s*(\d+)\s*$", re.I)
_NUM_ONLY_RE = re.compile(r"^\s*0*?(\d+)\s*$")


def normalize_num_or_pair(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    v = val.strip()
    m = _NUM_PAIR_RE.match(v)
    if m:
        return f"{int(m.group(1))}/{int(m.group(2))}"
    m = _NUM_ONLY_RE.match(v)
    if m:
        return str(int(m.group(1)))
    return v


def ffprobe_tags(src: Path, timeout_seconds: int | None = None) -> Dict[str, str]:
    if not FFPROBE:
        return {}
    try:
        p = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(src),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        data = json.loads(p.stdout)
        tags = {k.lower(): v for k, v in (data.get("format", {}).get("tags", {}) or {}).items()}
        for stream in data.get("streams", []):
            stream_tags = {k.lower(): v for k, v in (stream.get("tags", {}) or {}).items()}
            for key, value in stream_tags.items():
                tags.setdefault(key, value)
        return tags
    except Exception:
        return {}


def pick(tags: Dict[str, str], keys: List[str]) -> Optional[str]:
    for key in keys:
        if key in tags and str(tags[key]).strip():
            return str(tags[key]).strip()
    return None


def looks_va(artist: Optional[str], albumartist: Optional[str], compilation: Optional[str]) -> bool:
    if compilation and compilation.strip().lower() in {"1", "yes", "true"}:
        return True
    for value in (artist, albumartist):
        if value and value.strip().lower() in {"various artists", "va", "diverse artiesten"}:
            return True
    return False


def ffmpeg_convert(src: Path, dst: Path, timeout_seconds: int | None = None) -> None:
    cmd = [
        FFMPEG or "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(src),
        "-map",
        "0:a:0",
        "-map",
        "0:v?",
        "-c:a",
        "alac",
        "-c:v",
        "copy",
        "-disposition:v",
        "attached_pic",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-movflags",
        "use_metadata_tags",
        str(dst),
    ]
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
    )


def extract_cover_to_bytes(
    src_media: Path, timeout_seconds: int | None = None
) -> Optional[bytes]:
    try:
        from PIL import Image
    except ImportError:
        return None

    with tempfile.TemporaryDirectory() as temp_dir:
        out = Path(temp_dir) / "cover.jpg"
        for candidate in (src_media,):
            try:
                subprocess.run(
                    [
                        FFMPEG or "ffmpeg",
                        "-hide_banner",
                        "-nostdin",
                        "-y",
                        "-i",
                        str(candidate),
                        "-map",
                        "0:v:0",
                        "-frames:v",
                        "1",
                        str(out),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout_seconds,
                )
                if out.exists() and out.stat().st_size > 0:
                    with Image.open(out) as img:
                        if img.mode not in ("RGB", "L"):
                            img = img.convert("RGB")
                        img_resized = img.resize((600, 600), Image.Resampling.LANCZOS)
                        buffer = BytesIO()
                        img_resized.save(buffer, format="JPEG", quality=95)
                        data = buffer.getvalue()
                    return data
            except subprocess.CalledProcessError:
                pass
            except Exception:
                pass
    return None


def mutagen_tag(
    dst_m4a: Path, src_flac: Path, tags: Dict[str, str], timeout_seconds: int | None = None
) -> None:
    try:
        from mutagen.mp4 import MP4
        from mutagen.mp4 import MP4Cover
    except ImportError:
        return

    _ = timeout_seconds
    m = MP4(str(dst_m4a))

    title = pick(tags, ["title", "tit2", "©nam"]) or src_flac.stem
    artist = pick(tags, ["artist", "tpe1", "©art"])
    album = pick(tags, ["album", "talb", "©alb"])
    albumartist = pick(tags, ["albumartist", "album artist", "album_artist", "aartist", "aart"])
    genre = pick(tags, ["genre", "tcon", "©gen"])
    year = pick(tags, ["date", "year", "©day"])
    comment = pick(tags, ["comment", "description", "©cmt"])
    composer = pick(tags, ["composer", "tcom", "©wrt"])

    track = normalize_num_or_pair(pick(tags, ["tracknumber", "track", "trkn"]))
    disc = normalize_num_or_pair(pick(tags, ["discnumber", "disc", "disk"]))
    track_total = pick(tags, ["totaltracks", "tracktotal", "tracks", "tott"])
    disc_total = pick(tags, ["totaldiscs", "disctotal", "disks", "totd"])

    if not artist and albumartist:
        artist = albumartist

    m["\xa9nam"] = [title]
    if artist:
        m["\xa9ART"] = [artist]
    if album:
        m["\xa9alb"] = [album]
    if albumartist:
        m["aART"] = [albumartist]
    if genre:
        m["\xa9gen"] = [genre]
    if year:
        m["\xa9day"] = [year]
    if comment:
        m["\xa9cmt"] = [comment]
    if composer:
        m["\xa9wrt"] = [composer]

    def parse_pair(value: Optional[str], total: Optional[str]) -> Optional[Tuple[int, int]]:
        if not value:
            return None
        if "/" in value:
            n, t = value.split("/", 1)
            try:
                return int(n), int(t)
            except Exception:
                pass
        try:
            n = int(value)
            t = int(total) if (total and total.isdigit()) else 0
            return n, t
        except Exception:
            return None

    track_tuple = parse_pair(track, track_total)
    disc_tuple = parse_pair(disc, disc_total)
    if track_tuple:
        m["trkn"] = [track_tuple]
    if disc_tuple:
        m["disk"] = [disc_tuple]

    if looks_va(artist, albumartist, pick(tags, ["compilation"])):
        m["cpil"] = [1]

    if "stik" in m:
        del m["stik"]

    art = extract_cover_to_bytes(src_flac, timeout_seconds)
    if art:
        m["covr"] = [MP4Cover(art, imageformat=MP4Cover.FORMAT_JPEG)]

    m.save()


def convert_flac_to_alac(src: Path, dst: Path, timeout_seconds: int | None = None) -> None:
    if not FFMPEG or not FFPROBE:
        raise Flac2AlacError("Missing ffmpeg/ffprobe.")
    tags = ffprobe_tags(src, timeout_seconds=timeout_seconds)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_convert(src, dst, timeout_seconds=timeout_seconds)
        mutagen_tag(dst, src, tags, timeout_seconds=timeout_seconds)
    except subprocess.CalledProcessError as exc:
        output = exc.stdout or str(exc)
        raise Flac2AlacError(output) from exc
    except subprocess.TimeoutExpired as exc:
        raise Flac2AlacError("Conversion timed out.") from exc
    except Exception as exc:
        raise Flac2AlacError(f"Tagging failed: {exc}") from exc
