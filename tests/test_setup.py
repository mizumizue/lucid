from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class SetupDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)

        # Import setup module from skills/setup-lucid-memories/scripts/setup.py
        setup_py_path = (
            ROOT / "skills" / "setup-lucid-memories" / "scripts" / "setup.py"
        )
        spec = importlib.util.spec_from_file_location(
            "setup_script",
            setup_py_path,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.setup_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.setup_module)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ensure_directories_does_not_create_blobs_or_logs(self) -> None:
        self.setup_module.ensure_directories(self.repo_root)

        self.assertTrue((self.repo_root / ".db").is_dir())
        self.assertTrue((self.repo_root / "persona").is_dir())
        self.assertFalse((self.repo_root / "blobs").exists())
        self.assertFalse((self.repo_root / "logs").exists())

    def test_clean_legacy_directories_removes_empty_blobs_and_logs(self) -> None:
        blobs_dir = self.repo_root / "blobs"
        logs_dir = self.repo_root / "logs"
        blobs_dir.mkdir()
        logs_dir.mkdir()
        (blobs_dir / "empty_subdir").mkdir()

        self.assertTrue(blobs_dir.exists())
        self.assertTrue(logs_dir.exists())

        self.setup_module.clean_legacy_directories(self.repo_root)

        self.assertFalse(blobs_dir.exists())
        self.assertFalse(logs_dir.exists())

    def test_clean_legacy_directories_preserves_non_empty_directories(self) -> None:
        blobs_dir = self.repo_root / "blobs"
        blobs_dir.mkdir()
        test_file = blobs_dir / "payload.bin"
        test_file.write_bytes(b"data")

        self.setup_module.clean_legacy_directories(self.repo_root)

        self.assertTrue(blobs_dir.exists())
        self.assertTrue(test_file.exists())
        self.assertEqual(test_file.read_bytes(), b"data")

    def test_ensure_persona_initializes_empty_rules_for_new_setup(self) -> None:
        self.setup_module.ensure_persona(self.repo_root)

        user_rules_file = self.repo_root / "persona" / "user-rules.json"
        persona_file = self.repo_root / "persona" / "persona.json"
        gitkeep_file = self.repo_root / "persona" / ".gitkeep"

        self.assertTrue(gitkeep_file.exists())
        self.assertTrue(user_rules_file.exists())
        self.assertTrue(persona_file.exists())

        import json
        from lucid_memories.core import persona as persona_mod

        user_rules = json.loads(user_rules_file.read_text(encoding="utf-8"))
        persona = json.loads(persona_file.read_text(encoding="utf-8"))

        self.assertEqual(user_rules.get("rules"), [])
        self.assertEqual(persona.get("sections"), [])

        val = persona_mod.validate_persona(persona_file)
        self.assertTrue(val["ok"])

    def test_ensure_persona_preserves_existing_files(self) -> None:
        persona_dir = self.repo_root / "persona"
        persona_dir.mkdir(parents=True, exist_ok=True)
        user_rules_file = persona_dir / "user-rules.json"
        persona_file = persona_dir / "persona.json"

        user_rules_file.write_text('{"preserved_user": true}', encoding="utf-8")
        persona_file.write_text('{"preserved_persona": true}', encoding="utf-8")

        self.setup_module.ensure_persona(self.repo_root)

        self.assertEqual(user_rules_file.read_text(encoding="utf-8"), '{"preserved_user": true}')
        self.assertEqual(persona_file.read_text(encoding="utf-8"), '{"preserved_persona": true}')

    def test_setup_scripts_relocated_and_root_cleaned(self) -> None:
        # AC-001: Root should not contain setup.sh or setup.ps1
        self.assertFalse((ROOT / "setup.sh").exists(), "setup.sh should not exist at repository root")
        self.assertFalse((ROOT / "setup.ps1").exists(), "setup.ps1 should not exist at repository root")

        # AC-002: Target directory contains all setup runners
        scripts_dir = ROOT / "skills" / "setup-lucid-memories" / "scripts"
        self.assertTrue((scripts_dir / "setup.py").is_file(), "setup.py must exist in skills scripts")
        self.assertTrue((scripts_dir / "setup.sh").is_file(), "setup.sh must exist in skills scripts")
        self.assertTrue((scripts_dir / "setup.ps1").is_file(), "setup.ps1 must exist in skills scripts")

    def test_find_repo_root_resolves_correctly(self) -> None:
        # AC-003: find_repo_root finds actual repository root
        resolved = self.setup_module.find_repo_root()
        self.assertEqual(resolved.resolve(), ROOT.resolve())


if __name__ == "__main__":
    unittest.main()
