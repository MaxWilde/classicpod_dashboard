import os
import tempfile
import unittest

from album_art import CoverArtError, resolve_mount_root, resolve_track_abspath


class AlbumArtPathTests(unittest.TestCase):
    def test_resolve_mount_root_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(resolve_mount_root(tmp), os.path.abspath(tmp))

    def test_resolve_mount_root_itunesdb_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_dir = os.path.join(tmp, "iPod_Control", "iTunes")
            os.makedirs(db_dir, exist_ok=True)
            db_file = os.path.join(db_dir, "iTunesDB")
            with open(db_file, "w", encoding="utf-8") as fh:
                fh.write("x")
            self.assertEqual(resolve_mount_root(db_file), os.path.abspath(tmp))

    def test_resolve_track_abspath_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CoverArtError):
                resolve_track_abspath(tmp, "../../etc/passwd")

    def test_resolve_track_abspath_builds_inside_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = resolve_track_abspath(tmp, "/iPod_Control/Music/F00/ABCD.mp3")
            self.assertTrue(out.startswith(os.path.abspath(tmp)))


if __name__ == "__main__":
    unittest.main()
