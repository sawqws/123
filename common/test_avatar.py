"""Тесты аватаров: python3 common/test_avatar.py"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import avatar  # noqa: E402


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "не PNG"
    return struct.unpack(">II", head[16:24])


class Avatar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_make_every_bot(self):
        for name in avatar.BOTS:
            out = avatar.make(name, os.path.join(self.tmp, name + ".png"))
            self.assertEqual(png_size(out), (640, 640), name)

    def test_without_pillow(self):
        out = avatar.make("eye", os.path.join(self.tmp, "plain.png"), pil=False)
        self.assertEqual(png_size(out), (640, 640))
        out = avatar.custom(os.path.join(self.tmp, "plain2.png"), "Ж", "#ef4444", "#f59e0b", pil=False)
        self.assertEqual(png_size(out), (640, 640))

    def test_mark_is_drawn_and_centered(self):
        from PIL import Image
        img = Image.open(avatar.make("hdp", os.path.join(self.tmp, "c.png"))).convert("RGB")
        ink = avatar.rgb(avatar.BOTS["hdp"]["ink"])
        near = [(x, y) for y in range(0, 640, 4) for x in range(0, 640, 4)
                if sum(abs(a - b) for a, b in zip(img.getpixel((x, y)), ink)) < 40]
        self.assertGreater(len(near), 100)
        for x, y in near:  # знак — в центральной части, круг Telegram его не срежет
            self.assertLess((x - 320) ** 2 + (y - 320) ** 2, 250 ** 2)

    def test_custom_pastel_and_readable(self):
        bg1, bg2, ink = avatar.pastelize("#ff0000", "#0000ff")
        self.assertGreater(min(bg1), 200)  # фон — пастель, а не кислотный цвет
        self.assertGreaterEqual((avatar.lum(bg1) + 0.05) / (avatar.lum(ink) + 0.05), 4.5)
        out = avatar.custom(os.path.join(self.tmp, "c.png"), "в", "#0ea5e9", "#6366f1")
        self.assertEqual(png_size(out), (640, 640))

    def test_aliases(self):
        self.assertEqual(avatar.resolve("secretary"), "hub")
        self.assertEqual(avatar.resolve("HORO"), "hdp")
        with self.assertRaises(SystemExit):
            avatar.resolve("нет-такого")

    def test_apply_dry_run_without_env(self):
        lines = []
        with mock.patch.dict(os.environ, {"STUDIO_BOTS_DIR": self.tmp}), \
                mock.patch.object(avatar, "set_photo", side_effect=AssertionError("не должен ходить в сеть")):
            res = avatar.apply(dry=True, log=lines.append)
        self.assertEqual(set(res), set(avatar.BOTS))  # на этой машине /etc/*-bot.env нет
        self.assertTrue(all(v.startswith("пропущен") for v in res.values()), res)
        self.assertEqual(len(lines), len(res))

    def test_apply_with_env_and_studio_bots(self):
        env = os.path.join(self.tmp, "kpp.env")
        with open(env, "w") as f:
            f.write("KPP_TOKEN=123:SECRET\nKPP_ALLOWED=\n")
        bot_dir = os.path.join(self.tmp, "water")
        os.makedirs(bot_dir)
        with open(os.path.join(bot_dir, ".env"), "w") as f:
            f.write("BOT_TOKEN=456:SECRET\n")
        with open(os.path.join(self.tmp, "registry.json"), "w") as f:
            f.write('{"water": {"name": "Вода", "dir": "%s", "plan": {"colors": ["#0ea5e9", "#6366f1"]}}}' % bot_dir)
        calls = []
        bots = {k: dict(v) for k, v in avatar.BOTS.items()}
        bots["kpp"]["env"] = (env, "KPP_TOKEN")
        with mock.patch.dict(os.environ, {"STUDIO_BOTS_DIR": self.tmp}), mock.patch.object(avatar, "BOTS", bots), \
                mock.patch.object(avatar, "set_photo", lambda t, p: calls.append((t, p)) or (True, "")):
            res = avatar.apply(log=lambda s: None)
        self.assertEqual(res["kpp"], "ок")
        self.assertEqual(res["studio/water"], "ок")
        self.assertEqual(png_size(os.path.join(bot_dir, "data", "avatar.png")), (640, 640))  # botkit держит сам
        self.assertEqual([t for t, _ in calls], ["123:SECRET", "456:SECRET"])

    def test_cli_and_studio_wrapper(self):
        out = os.path.join(self.tmp, "cli.png")
        r = subprocess.run([sys.executable, os.path.join(HERE, "avatar.py"), "make", "coach", out],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(png_size(out), (640, 640))
        out = os.path.join(self.tmp, "studio.png")
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "studio", "avatar.py"), out, "Н",
                            "#0ea5e9", "#6366f1"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(png_size(out), (640, 640))


if __name__ == "__main__":
    unittest.main()
