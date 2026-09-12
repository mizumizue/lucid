from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest
from unittest.mock import patch

from lucid_memories.entrypoints.hook import sanitize_prompt, is_mojibake


class TestHookSanitization(unittest.TestCase):
    def test_is_mojibake_detection(self):
        self.assertFalse(is_mojibake("これは正常な日本語です"))
        self.assertFalse(is_mojibake("Hello world 123"))
        self.assertTrue(is_mojibake("隕∽ｻｶ繝ｻ莉墓ｧ倥↑縺ｩ縲√′蜈･縺｣縺ｦ縺・ｋ"))
        self.assertTrue(is_mojibake("荳願ｨ倥・隱ｬ譏弱・README.md縺ｨ縺励※谿九＠縺ｦ"))

    def test_sanitize_prompt_fallback_decoding(self):
        # A simple mojibake string that recovers via cp932 -> utf-8
        original = "要件"
        mojibake = "隕∽ｻｶ"
        self.assertEqual(sanitize_prompt(mojibake), original)

    def test_sanitize_prompt_clean_noop(self):
        clean = "要件・仕様のフォルダ構成"
        self.assertEqual(sanitize_prompt(clean), clean)


if __name__ == "__main__":
    unittest.main()
