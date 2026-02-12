from __future__ import annotations

from io import BytesIO
import os
import tempfile

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from album_art import CoverArtError, load_cover
from ipod_service import GpodError, add_tracks, delete_tracks, load_library


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB
ALLOWED_UPLOAD_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".aiff",
    ".aif",
    ".flac",
    ".ogg",
    ".opus",
    ".m4b",
}


def _is_supported_upload(filename: str, mimetype: str) -> bool:
    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
        return False
    mime = (mimetype or "").strip().lower()
    if mime.startswith("image/"):
        return False
    return True


@app.route("/")
def index() -> str:
    default_mountpoint = os.environ.get("DEFAULT_MOUNTPOINT", "/ipod")
    return render_template("index.html", default_mountpoint=default_mountpoint)


@app.route("/api/library")
def library() -> tuple[object, int] | object:
    mountpoint = request.args.get("mountpoint", "").strip()
    if not mountpoint:
        return jsonify({"error": "Query parameter 'mountpoint' is required."}), 400

    try:
        payload = load_library(mountpoint)
    except GpodError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "Unexpected server error."}), 500

    return jsonify(payload)


@app.route("/api/cover")
def cover() -> tuple[object, int] | object:
    mountpoint = request.args.get("mountpoint", "").strip()
    ipod_path = request.args.get("ipod_path", "").strip()
    if not mountpoint:
        return jsonify({"error": "Query parameter 'mountpoint' is required."}), 400
    if not ipod_path:
        return jsonify({"error": "Query parameter 'ipod_path' is required."}), 400

    try:
        image_bytes, mime_type = load_cover(mountpoint, ipod_path)
    except CoverArtError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        return jsonify({"error": "Unexpected server error."}), 500

    return send_file(
        BytesIO(image_bytes),
        mimetype=mime_type,
        as_attachment=False,
        max_age=3600,
        conditional=True,
    )


@app.route("/api/delete-tracks", methods=["POST"])
def remove_tracks() -> tuple[object, int] | object:
    payload = request.get_json(silent=True) or {}
    mountpoint = str(payload.get("mountpoint", "")).strip()
    ipod_paths = payload.get("ipod_paths", [])
    if not mountpoint:
        return jsonify({"error": "Field 'mountpoint' is required."}), 400
    if not isinstance(ipod_paths, list):
        return jsonify({"error": "Field 'ipod_paths' must be a list."}), 400

    try:
        result = delete_tracks(mountpoint, ipod_paths)
    except GpodError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "Unexpected server error."}), 500

    return jsonify(
        {
            "deleted_count": result["requested_count"],
            "message": f"Deleted {result['requested_count']} track(s).",
        }
    )


@app.route("/api/add-tracks", methods=["POST"])
def add_uploaded_tracks() -> tuple[object, int] | object:
    mountpoint = request.form.get("mountpoint", "").strip()
    files = request.files.getlist("files")

    if not mountpoint:
        return jsonify({"error": "Field 'mountpoint' is required."}), 400
    if not files:
        return jsonify({"error": "At least one file is required."}), 400

    uploaded_paths: list[str] = []
    skipped_count = 0
    with tempfile.TemporaryDirectory(prefix="classicpod_upload_") as upload_dir:
        for index, storage in enumerate(files):
            original_name = storage.filename or f"track_{index}"
            safe_name = secure_filename(original_name) or f"track_{index}"
            if not _is_supported_upload(safe_name, storage.mimetype or ""):
                skipped_count += 1
                continue
            path = os.path.join(upload_dir, f"{index:04d}_{safe_name}")
            storage.save(path)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                uploaded_paths.append(path)

        if not uploaded_paths:
            return jsonify({"error": "No supported audio files were uploaded."}), 400

        try:
            result = add_tracks(
                mountpoint=mountpoint,
                file_paths=uploaded_paths,
                convert_to_alac=True,
            )
        except GpodError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            return jsonify({"error": "Unexpected server error."}), 500

    converted_count = int(result.get("converted_count", 0))
    conversion_failed_count = int(result.get("conversion_failed_count", 0))
    conversion_failures = result.get("conversion_failures", [])
    added_count = int(result.get("requested_count", 0))
    skipped_suffix = f" Skipped {skipped_count} non-audio file(s)." if skipped_count else ""
    if converted_count:
        phase_message = (
            f"Phase 1: converted {converted_count} FLAC file(s) to ALAC. "
            f"Phase 2: copied {added_count} file(s) to iPod."
        )
    else:
        phase_message = f"Phase 1: no FLAC conversion needed. Phase 2: copied {added_count} file(s) to iPod."
    if conversion_failed_count:
        failed_names = [
            str(item.get("source", "")).strip()
            for item in conversion_failures
            if isinstance(item, dict)
        ]
        failed_names = [name for name in failed_names if name]
        listed = ", ".join(failed_names[:3])
        if len(failed_names) > 3:
            listed = f"{listed}, ..."
        details = f" ({listed})" if listed else ""
        phase_message = (
            f"{phase_message} Conversion failed for {conversion_failed_count} FLAC file(s){details}; "
            "those files were added without conversion."
        )
    return jsonify(
        {
            "added_count": added_count,
            "converted_count": converted_count,
            "conversion_failed_count": conversion_failed_count,
            "skipped_count": skipped_count,
            "message": f"{phase_message}{skipped_suffix}",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
