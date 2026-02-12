import io
import unittest
from unittest.mock import patch

try:
    from app import app
except ModuleNotFoundError:
    app = None


@unittest.skipUnless(app is not None, "Flask is not installed in this environment")
class AddTracksApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.config["TESTING"] = True
        self.client = app.test_client()

    @patch("app.add_tracks")
    def test_add_tracks_accepts_audio_files(self, mock_add_tracks) -> None:
        mock_add_tracks.return_value = {"requested_count": 1, "converted_count": 0}
        response = self.client.post(
            "/api/add-tracks",
            data={
                "mountpoint": "/ipod",
                "convert_to_alac": "false",
                "files": (io.BytesIO(b"abc"), "song.mp3"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["added_count"], 1)
        self.assertEqual(payload["skipped_count"], 0)
        self.assertTrue(mock_add_tracks.called)

    @patch("app.add_tracks")
    def test_add_tracks_rejects_non_audio_only_upload(self, mock_add_tracks) -> None:
        response = self.client.post(
            "/api/add-tracks",
            data={
                "mountpoint": "/ipod",
                "convert_to_alac": "false",
                "files": (io.BytesIO(b"abc"), "notes.txt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("No supported audio files", payload["error"])
        self.assertFalse(mock_add_tracks.called)

    @patch("app.add_tracks")
    def test_add_tracks_skips_non_audio_in_mixed_upload(self, mock_add_tracks) -> None:
        mock_add_tracks.return_value = {"requested_count": 1, "converted_count": 0}
        response = self.client.post(
            "/api/add-tracks",
            data={
                "mountpoint": "/ipod",
                "files": [
                    (io.BytesIO(b"a"), "track.flac"),
                    (io.BytesIO(b"b"), "cover.jpg"),
                ],
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["added_count"], 1)
        self.assertEqual(payload["skipped_count"], 1)
        kwargs = mock_add_tracks.call_args.kwargs
        self.assertEqual(len(kwargs["file_paths"]), 1)
        self.assertTrue(kwargs["file_paths"][0].endswith(".flac"))


if __name__ == "__main__":
    unittest.main()
