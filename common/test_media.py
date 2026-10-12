"""Тесты common/media.py: тип сообщения, видео (кадры и речь), understand. Запуск: python3 common/test_media.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg"))


class MediaTest(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(media.kind({"voice": {"file_id": "x"}}), "voice")
        self.assertEqual(media.kind({"audio": {"file_id": "x"}}), "voice")
        self.assertEqual(media.kind({"photo": [{"file_id": "x"}]}), "photo")
        self.assertEqual(media.kind({"document": {"file_id": "x", "mime_type": "image/png"}}), "photo")
        self.assertEqual(media.kind({"video": {"file_id": "x"}}), "video")
        self.assertEqual(media.kind({"video_note": {"file_id": "x"}}), "video")
        self.assertEqual(media.kind({"animation": {"file_id": "x"}}), "video")
        self.assertEqual(media.kind({"document": {"file_id": "x", "mime_type": "video/mp4"}}), "video")
        self.assertIsNone(media.kind({"text": "привет"}))

    def test_big_video_gives_cover(self):
        msg = {"video": {"file_id": "v", "file_size": 50 * 1024 * 1024, "duration": 30, "thumbnail": {"file_id": "t"}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(media, "download", side_effect=lambda t, f, d: d):
            v = media.video("T", msg, tmp)
        self.assertEqual(v["frames"], [os.path.join(tmp, "cover.jpg")])
        self.assertTrue(v["cover_only"])
        with self.assertRaises(media.MediaError):
            media.video("T", {"video": {"file_id": "v", "file_size": 50 * 1024 * 1024}})

    @unittest.skipUnless(HAS_FFMPEG, "нет ffmpeg")
    def test_video_frames_and_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.mp4")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-shortest", "-pix_fmt", "yuv420p",
                            src], check=True)

            def fake_download(token, fid, dst):
                shutil.copy(src, dst)
                return dst
            out = os.path.join(tmp, "out")
            with mock.patch.object(media, "download", side_effect=fake_download), \
                    mock.patch.object(media, "transcribe", return_value="вот номер машины"):
                v = media.video("T", {"video": {"file_id": "v", "duration": 4}, "caption": "смотри"}, out, frames=3)
                u = media.understand("T", {"video_note": {"file_id": "v", "duration": 4}}, out)
            self.assertEqual(len(v["frames"]), 3)
            self.assertTrue(all(os.path.getsize(f) > 0 for f in v["frames"]))
            self.assertEqual(v["text"], "вот номер машины")
            self.assertEqual(u["kind"], "video")
            self.assertIn("вот номер машины", u["text"])
            self.assertTrue(u["images"])

    def test_understand_voice_photo_text(self):
        with mock.patch.object(media, "voice_text", return_value="купить хлеб"):
            self.assertEqual(media.understand("T", {"voice": {"file_id": "x"}})["text"], "купить хлеб")
        with mock.patch.object(media, "photo_path", return_value="/tmp/p.jpg"):
            u = media.understand("T", {"photo": [{"file_id": "x"}], "caption": "что это"})
        self.assertEqual((u["kind"], u["text"], u["images"]), ("photo", "что это", ["/tmp/p.jpg"]))
        self.assertEqual(media.understand("T", {"text": "привет"})["kind"], "text")


if __name__ == "__main__":
    unittest.main()
