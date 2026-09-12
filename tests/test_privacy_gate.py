#!/usr/bin/env python3
"""Tests for privacy gate and Git hooks."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from lucid_memories.core import gate, privacy_gate
from lucid_memories.entrypoints import hook


class PrivacyGateTests(unittest.TestCase):
    def test_sensitive_path_detection(self) -> None:
        sensitive_cases = [
            "persona/persona.json",
            "persona/user-rules.json",
            ".env",
            ".env.production",
            ".env.local",
            "id_rsa",
            "id_ed25519",
            "secrets/private.pem",
            "cert.key",
            "client_secret.json",
            "google_credentials.json",
            "local_settings.py",
        ]
        for path in sensitive_cases:
            is_sens, reason = privacy_gate.check_sensitive_path(path)
            self.assertTrue(is_sens, f"Expected {path} to be flagged as sensitive, got: {reason}")

    def test_safe_path_allowlist(self) -> None:
        safe_cases = [
            ".env.example",
            ".env.template",
            "persona/.gitkeep",
            "src/main.py",
            "README.md",
            "docs/requirements/REQ-0001.md",
        ]
        for path in safe_cases:
            is_sens, reason = privacy_gate.check_sensitive_path(path)
            self.assertFalse(is_sens, f"Expected {path} to be safe, but flagged: {reason}")

    def test_local_only_repo_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            # Not a git repo -> local only
            self.assertTrue(privacy_gate.is_local_only_repo(repo_path))

            # Init empty git repo with no remotes
            subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
            self.assertTrue(privacy_gate.is_local_only_repo(repo_path))

            # Add remote -> not local only
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/repo.git"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
            self.assertFalse(privacy_gate.is_local_only_repo(repo_path))

            # Set lucid.localOnly = true
            subprocess.run(
                ["git", "config", "lucid.localOnly", "true"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
            self.assertTrue(privacy_gate.is_local_only_repo(repo_path))

    def test_check_staged_files_with_violations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/repo.git"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
            # Create a sensitive file
            p_dir = repo_path / "persona"
            p_dir.mkdir(parents=True, exist_ok=True)
            p_json = p_dir / "persona.json"
            p_json.write_text("{}", encoding="utf-8")

            # Stage it
            subprocess.run(["git", "add", "persona/persona.json"], cwd=str(repo_path), check=True, capture_output=True)

            check = privacy_gate.check_staged_files(repo_path=repo_path)
            self.assertFalse(check["ok"])
            self.assertGreater(len(check["violations"]), 0)

            # Test gate.shell_permission detects git commit
            perm = gate.shell_permission("git commit -m 'test'", repo_root=repo_path)
            self.assertEqual(perm.get("permission"), "ask")
            self.assertIn("Personal or sensitive information detected", perm.get("user_message", ""))

    def test_gate_git_add_sensitive_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/repo.git"],
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )
            # git add .env
            perm = gate.shell_permission("git add .env", repo_root=repo_path)
            self.assertEqual(perm.get("permission"), "ask")
            self.assertIn("Attempting to stage sensitive file", perm.get("user_message", ""))

            # git add safe file
            perm_safe = gate.shell_permission("git add README.md", repo_root=repo_path)
            self.assertEqual(perm_safe, {})


if __name__ == "__main__":
    unittest.main()
