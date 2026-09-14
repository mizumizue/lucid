#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class WorkspacePersonaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api, persona, workspace_persona
        from lucid_memories.entrypoints import hook

        self.api = api
        self.hook = hook
        self.persona = persona
        self.workspace_persona = workspace_persona
        self.cid = "55555555-5555-5555-5555-555555555555"
        self.ws = "C:/workspace-app"
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")
        source = Path(self.tmp.name) / "rules.json"
        source.write_text(
            json.dumps(
                {
                    "rules": [
                        {
                            "id": "language",
                            "title": "Language",
                            "content": "Always respond in Japanese.",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.persona.export_user_rules(source)
        self.workspace_persona.ensure_type_templates()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_pending_binding_injects_global_only(self) -> None:
        proposed = self.workspace_persona.propose_binding(
            workspace=self.ws,
            type_id="app-dev",
            rationale="package.json found",
            confidence=0.8,
        )
        self.assertEqual(proposed["binding"]["status"], "pending")
        injection = self.persona.get_injection(workspace_root=self.ws)
        self.assertIn("[global persona]", injection["content"])
        self.assertNotIn("[workspace persona:", injection["content"])
        self.assertFalse(injection["layers"]["type"])

    def test_confirmed_binding_injects_type_overlay(self) -> None:
        self.workspace_persona.propose_binding(
            workspace=self.ws,
            type_id="app-dev",
            rationale="application project",
            confidence=0.8,
        )
        confirmed = self.workspace_persona.confirm_binding(workspace=self.ws, created_by="user")
        self.assertEqual(confirmed["binding"]["status"], "confirmed")
        injection = self.persona.get_injection(workspace_root=self.ws)
        self.assertIn("[workspace persona: app-dev]", injection["content"])
        self.assertIn("Prefer the smallest correct change.", injection["content"])
        self.assertTrue(injection["layers"]["type"])

    def test_workspace_overlay_is_injected_when_confirmed(self) -> None:
        self.workspace_persona.propose_binding(
            workspace=self.ws,
            type_id="app-dev",
            rationale="application project",
        )
        self.workspace_persona.confirm_binding(workspace=self.ws, created_by="user")
        self.workspace_persona.set_workspace_overlay_section(
            workspace=self.ws,
            section_id="repo",
            title="Repo rule",
            content="Use pnpm instead of npm.",
        )
        injection = self.persona.get_injection(workspace_root=self.ws)
        self.assertIn("[workspace overlay]", injection["content"])
        self.assertIn("Use pnpm instead of npm.", injection["content"])
        self.assertTrue(injection["layers"]["workspace"])

    def test_override_changes_type_and_records_event(self) -> None:
        self.workspace_persona.propose_binding(
            workspace=self.ws,
            type_id="app-dev",
            rationale="initial",
        )
        self.workspace_persona.confirm_binding(workspace=self.ws, created_by="user")
        overridden = self.workspace_persona.override_binding(
            workspace=self.ws,
            type_id="notes",
            created_by="user",
        )
        self.assertEqual(overridden["binding"]["type_id"], "notes")
        events = self.workspace_persona.list_binding_events(workspace=self.ws, limit=10)
        actions = [event["action"] for event in events]
        self.assertIn("override", actions)

    def test_reject_clears_pending_binding(self) -> None:
        self.workspace_persona.propose_binding(
            workspace=self.ws,
            type_id="app-dev",
            rationale="temporary",
        )
        rejected = self.workspace_persona.reject_binding(workspace=self.ws, created_by="user")
        self.assertTrue(rejected["ok"])
        self.assertIsNone(self.workspace_persona.get_binding(self.ws))

    def test_infer_meta_for_lucid_layout(self) -> None:
        meta_root = Path(self.tmp.name) / "meta-workspace"
        (meta_root / "src" / "lucid_memories").mkdir(parents=True)
        inferred = self.workspace_persona.infer_workspace_type(str(meta_root))
        self.assertEqual(inferred["type_id"], "meta")
        self.assertGreaterEqual(inferred["confidence"], 0.9)

    def test_hook_injects_layered_persona_after_confirm(self) -> None:
        self.workspace_persona.propose_binding(
            workspace=self.ws,
            type_id="infra",
            rationale="docker-compose present",
        )
        self.workspace_persona.confirm_binding(workspace=self.ws, created_by="user")
        result = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "workspace_roots": [self.ws],
                "prompt": "deploy please",
            }
        )
        ctx = result.get("additional_context") or ""
        self.assertIn("[global persona]", ctx)
        self.assertIn("[workspace persona: infra]", ctx)
        self.assertIn("Prefer idempotent, reversible changes.", ctx)

    def test_api_persona_workspace_actions(self) -> None:
        types = self.api.persona_workspace("types")
        self.assertIn("app-dev", types["types"])
        inferred = self.api.persona_workspace("infer", workspace=self.ws)
        self.assertTrue(inferred["ok"])
        status = self.api.persona_workspace("status", workspace=self.ws)
        self.assertIsNone(status["binding"])


if __name__ == "__main__":
    unittest.main()
