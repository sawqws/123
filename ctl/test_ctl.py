"""Тесты «Контролёра»: python3 ctl/test_ctl.py"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import install  # noqa: E402
import pace  # noqa: E402
from turn import Turn, entries, is_prompt  # noqa: E402


def ent(kind, content, t, **kw):
    return {"type": kind, "timestamp": f"2026-10-22T10:{t // 60:02d}:{t % 60:02d}Z", "uuid": f"u{t}",
            "message": {"role": kind, "content": content}, **kw}


def use(i, name="Bash", t=0, **inp):
    return ent("assistant", [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}], t)


def res(i, text, t, err=False):
    return ent("user", [{"type": "tool_result", "tool_use_id": f"t{i}", "content": text, "is_error": err}], t)


class TurnTest(unittest.TestCase):
    def test_prompt_detection(self):
        self.assertTrue(is_prompt(ent("user", "сделай задания", 0)))
        self.assertFalse(is_prompt(ent("user", "<task-notification>…", 0)))
        self.assertFalse(is_prompt(res(1, "ok", 0)))
        self.assertFalse(is_prompt(ent("user", "x", 0, isMeta=True)))

    def test_turn_after_last_prompt(self):
        log = [ent("user", "старое", 0), use(0, t=1), res(0, "a", 2),
               ent("user", "новое", 10), use(1, t=20, description="тест"), res(1, "Traceback", 25, err=True),
               ent("assistant", [{"type": "text", "text": "Готово"}], 30)]
        t = Turn(log)
        self.assertEqual(t.prompt, "новое")
        self.assertEqual(len(t.tools), 1)
        self.assertTrue(t.tools[0]["error"])
        self.assertEqual(t.final, "Готово")
        self.assertEqual(t.gaps, [10])
        self.assertIn("ОШИБКА: Traceback", t.log())

    def test_handback_is_final(self):
        t = Turn([ent("user", "бриф", 0), use(1, "SubagentHandback", 5, message="отчёт агента")])
        self.assertEqual(t.final, "отчёт агента")

    def test_idle_not_counted(self):
        t = Turn([ent("user", "q", 0), use(1, t=60), res(1, "", 2000)])  # 32 мин ждал агента
        self.assertLess(t.active, 7 * 60)

    def test_tail_read(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for i in range(3):
                f.write(json.dumps(ent("user", f"p{i}", i)) + "\n")
        self.assertEqual(len(entries(f.name, tail=10 ** 6)), 3)
        self.assertEqual(entries("/nonexistent"), [])


class PaceTest(unittest.TestCase):
    def turn(self, tools):
        log = [ent("user", "q", 0)]
        for i, (t, err) in enumerate(tools):
            log += [use(i, t=t), res(i, "e" if err else "ok", t + 1, err)]
        return Turn(log)

    def test_quiet_when_fast(self):
        t = self.turn([(10, False), (20, False)])
        self.assertEqual(pace.advice(t, {}, t.started + 60), [])

    def test_long_and_rate_limit(self):
        t = self.turn([(m * 60, False) for m in range(1, 14)])
        st = {}
        tips = pace.advice(t, st, t.last_at + 30)
        self.assertTrue(any("мин" in x for x in tips))
        self.assertEqual(pace.advice(t, st, t.last_at + 60), [])  # не чаще раза в 10 мин

    def test_think_and_repeat(self):
        t = self.turn([(10, True), (20, True), (230, True)])
        tips = " ".join(pace.advice(t, {}, t.last_at + 1))
        self.assertIn("думал", tips)
        self.assertIn("ошибки подряд", tips)


class InstallTest(unittest.TestCase):
    def test_merge_idempotent_and_keeps_others(self):
        other = {"hooks": [{"type": "command", "command": "~/.claude/stop-hook-git-check.sh"}]}
        cfg = {"model": "x", "hooks": {"Stop": [other]}}
        a = install.merge(json.loads(json.dumps(cfg)))
        b = install.merge(json.loads(json.dumps(a)))
        self.assertEqual(a, b)
        self.assertIn(other, a["hooks"]["Stop"])
        self.assertEqual(len(a["hooks"]["Stop"]), 2)
        self.assertEqual(install.merge(a, add=False), cfg)


if __name__ == "__main__":
    unittest.main()
