import tempfile
import unittest
from unittest.mock import patch

from ipod_service import (
    GpodError,
    add_tracks,
    delete_tracks,
    load_library,
    parse_gpod_output,
)


SAMPLE_JSON = """{
  "ipod_data": {
    "device": {
      "generation": "iPod Video (1st Gen.)",
      "model_name": "iPod Video",
      "model_number": "A002"
    },
    "playlists": {
      "items": [
        {
          "name": "iPod",
          "type": "master",
          "tracks": [
            {
              "id": 10,
              "ipod_path": "/iPod_Control/Music/F00/ABCD.mp3",
              "title": "Song A",
              "artist": "Artist A",
              "album": "Album A",
              "year": 2005,
              "tracklen": 180000,
              "size": 123456,
              "artwork": true,
              "playcount": 2,
              "bitrate": 192
            }
          ]
        }
      ]
    }
  }
}"""


class ParseOutputTests(unittest.TestCase):
    def test_parse_direct_json(self) -> None:
        data = parse_gpod_output(SAMPLE_JSON)
        self.assertIn("ipod_data", data)

    def test_parse_with_leading_noise(self) -> None:
        data = parse_gpod_output("noise\\n" + SAMPLE_JSON + "\\ntrailer")
        self.assertIn("ipod_data", data)

    def test_parse_raises_when_not_json(self) -> None:
        with self.assertRaises(GpodError):
            parse_gpod_output("not json")

class LoadLibraryTests(unittest.TestCase):
    @patch("ipod_service._run_gpod_ls_with_recovery")
    def test_load_library_happy_path(self, mock_run_ls) -> None:
        mock_run_ls.return_value = (
            "/ipod",
            unittest.mock.Mock(returncode=0, stdout=SAMPLE_JSON, stderr=""),
        )

        payload = load_library("/ipod")

        self.assertEqual(payload["track_count"], 1)
        self.assertEqual(payload["artist_count"], 1)
        self.assertEqual(payload["album_count"], 1)
        self.assertEqual(payload["tracks"][0]["title"], "Song A")
        self.assertTrue(payload["tracks"][0]["artwork"])

    @patch("ipod_service._run_gpod_ls_with_recovery", side_effect=GpodError("Mountpoint does not exist: /missing"))
    def test_load_library_missing_path(self, _mock_run_ls) -> None:
        with self.assertRaises(GpodError):
            load_library("/missing")

    @patch("ipod_service._discover_mountpoint_candidates")
    @patch("ipod_service._run_gpod_ls_once")
    def test_load_library_recovers_after_replug(self, mock_ls_once, mock_discover) -> None:
        mock_discover.return_value = ["/ipod", "/media-host/max/IPOD"]
        mock_ls_once.side_effect = [
            unittest.mock.Mock(returncode=1, stdout="", stderr="Couldn't find an iPod database on /ipod."),
            unittest.mock.Mock(returncode=0, stdout=SAMPLE_JSON, stderr=""),
        ]

        payload = load_library("/ipod")

        self.assertEqual(payload["mountpoint"], "/media-host/max/IPOD")
        self.assertEqual(payload["track_count"], 1)


class DeleteTracksTests(unittest.TestCase):
    @patch("ipod_service.os.path.exists", return_value=True)
    @patch("ipod_service.subprocess.run")
    def test_delete_tracks_happy_path(self, mock_run, _mock_exists) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""

        result = delete_tracks(
            "/ipod",
            ["/iPod_Control/Music/F00/A.mp3", "iPod_Control/Music/F00/A.mp3", "  ", "/../etc/passwd"],
        )

        self.assertEqual(result["requested_count"], 1)
        command = mock_run.call_args[0][0]
        self.assertEqual(command[0], "gpod-rm")
        self.assertEqual(command[1], "-M")
        self.assertEqual(command[2], "/ipod")
        self.assertEqual(command[3], "/iPod_Control/Music/F00/A.mp3")

    @patch("ipod_service.os.path.exists", return_value=True)
    @patch("ipod_service.subprocess.run")
    def test_delete_tracks_multiple_paths_single_command(self, mock_run, _mock_exists) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""
        delete_tracks(
            "/ipod",
            ["/iPod_Control/Music/F00/A.mp3", "/iPod_Control/Music/F00/B.mp3"],
        )
        command = mock_run.call_args[0][0]
        self.assertEqual(command[:3], ["gpod-rm", "-M", "/ipod"])
        self.assertEqual(command[3:], ["/iPod_Control/Music/F00/A.mp3", "/iPod_Control/Music/F00/B.mp3"])

    @patch("ipod_service.os.path.exists", return_value=True)
    def test_delete_tracks_empty_paths(self, _mock_exists) -> None:
        with self.assertRaises(GpodError):
            delete_tracks("/ipod", [])

    @patch("ipod_service.os.path.exists", return_value=True)
    @patch("ipod_service.subprocess.run")
    def test_delete_tracks_subprocess_failure(self, mock_run, _mock_exists) -> None:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "failed"
        with self.assertRaises(GpodError):
            delete_tracks("/ipod", ["/iPod_Control/Music/F00/A.mp3"])

    @patch("ipod_service.os.path.exists", return_value=True)
    @patch("ipod_service.subprocess.run")
    def test_delete_tracks_accepts_ipod_ids(self, mock_run, _mock_exists) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""

        result = delete_tracks("/ipod", ["521", "9999"])
        self.assertEqual(result["requested_count"], 2)
        command = mock_run.call_args[0][0]
        self.assertEqual(command[:3], ["gpod-rm", "-M", "/ipod"])
        self.assertEqual(command[3:], ["521", "9999"])


class AddTracksTests(unittest.TestCase):
    @patch("ipod_service.subprocess.run")
    def test_add_tracks_happy_path(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as mountpoint:
            with tempfile.NamedTemporaryFile(suffix=".mp3") as track:
                result = add_tracks(mountpoint, [track.name], convert_to_alac=False)

        self.assertEqual(result["requested_count"], 1)
        self.assertEqual(result["converted_count"], 0)
        command = mock_run.call_args[0][0]
        self.assertEqual(command[0], "gpod-cp")
        self.assertEqual(command[1], "-M")
        self.assertEqual(command[2], mountpoint)
        self.assertEqual(command[3], track.name)

    @patch("ipod_service.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("ipod_service.subprocess.run")
    def test_add_tracks_with_flac_conversion(self, mock_run, _mock_which) -> None:
        success = unittest.mock.Mock(returncode=0, stdout="ok", stderr="")
        mock_run.return_value = success

        with tempfile.TemporaryDirectory() as mountpoint:
            with tempfile.NamedTemporaryFile(suffix=".flac") as track:
                result = add_tracks(mountpoint, [track.name], convert_to_alac=True)

        self.assertEqual(result["requested_count"], 1)
        self.assertEqual(result["converted_count"], 1)
        commands = [call.args[0] for call in mock_run.call_args_list]
        ffmpeg_cmd = next((cmd for cmd in commands if cmd and cmd[0] == "/usr/bin/ffmpeg"), None)
        gpod_cp_cmd = next((cmd for cmd in commands if cmd and cmd[0] == "gpod-cp"), None)
        self.assertIsNotNone(ffmpeg_cmd)
        self.assertIsNotNone(gpod_cp_cmd)
        self.assertEqual(gpod_cp_cmd[1], "-M")
        self.assertIn(".m4a", gpod_cp_cmd[3])

    @patch("ipod_service.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("ipod_service._run_gpod_cp")
    @patch("ipod_service._convert_flac_to_alac")
    def test_add_tracks_two_phase_convert_then_copy(self, mock_convert, mock_copy, _mock_which) -> None:
        mock_copy.return_value = unittest.mock.Mock(returncode=0, stdout="ok", stderr="")

        def fake_convert(_src: str, dst: str, timeout_seconds: int = 600) -> None:
            _ = timeout_seconds
            with open(dst, "wb") as fh:
                fh.write(b"m4a")

        mock_convert.side_effect = fake_convert

        with tempfile.TemporaryDirectory() as mountpoint:
            with tempfile.NamedTemporaryFile(suffix=".flac") as flac1:
                with tempfile.NamedTemporaryFile(suffix=".mp3") as mp3:
                    with tempfile.NamedTemporaryFile(suffix=".flac") as flac2:
                        result = add_tracks(mountpoint, [flac1.name, mp3.name, flac2.name], convert_to_alac=True)

        self.assertEqual(result["converted_count"], 2)
        self.assertEqual(mock_convert.call_count, 2)
        self.assertEqual(mock_copy.call_count, 1)
        copy_sources = mock_copy.call_args[0][1]
        self.assertEqual(copy_sources[1], mp3.name)
        self.assertTrue(copy_sources[0].endswith(".m4a"))
        self.assertTrue(copy_sources[2].endswith(".m4a"))

    @patch("ipod_service.shutil.which", return_value=None)
    def test_add_tracks_requires_ffmpeg_when_conversion_enabled(self, _mock_which) -> None:
        with tempfile.TemporaryDirectory() as mountpoint:
            with tempfile.NamedTemporaryFile(suffix=".flac") as track:
                with self.assertRaises(GpodError):
                    add_tracks(mountpoint, [track.name], convert_to_alac=True)


if __name__ == "__main__":
    unittest.main()
