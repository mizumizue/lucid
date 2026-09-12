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


class PersonaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories import api, hook, persona

        self.api = api
        self.hook = hook
        self.persona = persona
        self.cid = "44444444-4444-4444-4444-444444444444"
        self.api.ensure_session(self.cid, workspace_roots=["C:/workspace-a"], status="active")
        self.source = Path(self.tmp.name) / "rules.json"
        self.source.write_text(
            json.dumps(
                {
                    "rules": [
                        {"id": "language", "title": "Language", "content": "Always respond in Japanese."},
                        {"id": "clarity", "title": "Clarity", "content": "Explain the reasoning clearly."},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_export_and_injection_are_global(self) -> None:
        exported = self.persona.export_user_rules(self.source)
        self.assertEqual(exported["scope"], "user")
        self.assertIsNone(exported["workspace_root"])
        first = self.persona.get_injection()
        self.assertIn("Always respond in Japanese.", first["content"])

        result_a = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "workspace_roots": ["C:/workspace-a"],
                "prompt": "実装してください",
            }
        )
        result_b = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "workspace_roots": ["D:/workspace-b"],
                "prompt": "直してください",
            }
        )
        self.assertEqual(result_a["additional_context"], result_b["additional_context"])
        self.assertIn("Language", result_a["additional_context"])

        current = self.persona.load_persona()
        current["sections"][0]["content"] = "Always answer in Japanese and reload this change."
        self.persona.save_persona(current)
        result_c = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "workspace_roots": ["E:/workspace-c"],
            }
        )
        self.assertIn("reload this change", result_c["additional_context"])

    def test_budget_prefers_earlier_sections_and_reports_omissions(self) -> None:
        source = {
            "rules": [
                {"id": "first", "title": "First", "content": "Keep this section."},
                {"id": "large", "title": "Large", "content": "x " * 5000},
            ]
        }
        persona = self.persona.build_persona(source, token_budget=1_000)
        rendered = self.persona.render_persona(persona)
        self.assertLessEqual(rendered["token_estimate"], 1_000)
        self.assertIn("large", rendered["omitted_sections"])
        self.assertTrue(rendered["over_budget"])

    def test_invalid_persona_is_reported(self) -> None:
        path = Path(self.tmp.name) / "persona" / "persona.json"
        path.parent.mkdir()
        path.write_text("{broken", encoding="utf-8")
        result = self.persona.validate_persona(path)
        self.assertFalse(result["ok"])

        hook_result = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "prompt": "安全に続行してください",
            }
        )
        self.assertEqual(hook_result, {})

    def test_update_and_set_sections(self) -> None:
        self.persona.export_user_rules(self.source)
        self.persona.update_section("language", content="日本語で答える。")
        self.persona.set_section(
            section_id="safety",
            title="Safety",
            content="Do not invent facts.",
            priority=10,
        )
        current = self.persona.load_persona()
        sections = {section["id"]: section for section in current["sections"]}
        self.assertEqual(sections["language"]["content"], "日本語で答える。")
        self.assertEqual(sections["safety"]["priority"], 10)

    def test_detector_rejects_local_and_one_off_instructions(self) -> None:
        self.assertIsNotNone(self.persona.detect_preference("日本語で答えてください。"))
        self.assertIsNotNone(self.persona.detect_preference("今後は説明を簡潔にしてください。"))
        self.assertIsNone(self.persona.detect_preference("このWorkspaceでは日本語で答えてください。"))
        self.assertIsNone(self.persona.detect_preference("このバグを直してください。"))

    def test_two_independent_conversations_apply_and_rollback(self) -> None:
        empty_source = Path(self.tmp.name) / "empty-rules.json"
        empty_source.write_text(json.dumps({"rules": []}), encoding="utf-8")
        self.persona.export_user_rules(empty_source)

        first = self.persona.record_preference_candidate(
            "日本語で答えてください。",
            conversation_id="conversation-one",
            source_event_id="event-one",
        )
        same_conversation = self.persona.record_preference_candidate(
            "日本語で答えてください。",
            conversation_id="conversation-one",
            source_event_id="event-two",
        )
        self.assertEqual(first["candidate"]["evidence_count"], 1)
        self.assertEqual(same_conversation["candidate"]["evidence_count"], 1)

        second = self.persona.record_preference_candidate(
            "日本語で答えてください。",
            conversation_id="conversation-two",
            source_event_id="event-three",
        )
        self.assertEqual(second["candidate"]["evidence_count"], 2)

        applied = self.persona.apply_ready_candidates()
        self.assertEqual(len(applied["applied"]), 1)
        revision_id = applied["applied"][0]["revision_id"]
        injected = self.persona.get_injection()["content"]
        self.assertIn("日本語で答えてください", injected)

        rollback = self.persona.rollback_persona_revision(revision_id)
        self.assertTrue(rollback["ok"])
        self.assertNotIn("日本語で答えてください", self.persona.get_injection()["content"])

    def test_hook_worker_applies_after_second_conversation(self) -> None:
        empty_source = Path(self.tmp.name) / "empty-rules.json"
        empty_source.write_text(json.dumps({"rules": []}), encoding="utf-8")
        self.persona.export_user_rules(empty_source)
        for index, conversation_id in enumerate(("hook-conversation-one", "hook-conversation-two")):
            self.hook.handle_hook(
                {
                    "hook_event_name": "beforeSubmitPrompt",
                    "conversation_id": conversation_id,
                    "prompt": "今後は日本語で答えてください。",
                    "workspace_roots": [f"C:/workspace-{index}"],
                }
            )
            self.hook.handle_hook(
                {
                    "hook_event_name": "postToolUse",
                    "conversation_id": conversation_id,
                    "generation_id": f"generation-{index}",
                    "workspace_roots": [f"C:/workspace-{index}"],
                }
            )
        self.assertIn("日本語で答えてください", self.persona.get_injection()["content"])

    def test_duplicate_and_conflicting_preferences_are_not_applied(self) -> None:
        empty_source = Path(self.tmp.name) / "empty-rules.json"
        empty_source.write_text(json.dumps({"rules": []}), encoding="utf-8")
        self.persona.export_user_rules(empty_source)
        current = self.persona.load_persona()
        current["sections"] = [
            {
                "id": "base-language",
                "title": "Language",
                "content": "応答は日本語にする。",
                "priority": 0,
            }
        ]
        self.persona.save_persona(current)

        for conversation_id, event_id in (
            ("duplicate-one", "duplicate-event-one"),
            ("duplicate-two", "duplicate-event-two"),
        ):
            self.persona.record_preference_candidate(
                "日本語で答えてください。",
                conversation_id=conversation_id,
                source_event_id=event_id,
            )
        duplicate_result = self.persona.apply_ready_candidates()
        self.assertEqual(duplicate_result["applied"], [])
        self.assertEqual(duplicate_result["skipped"][0]["status"], "skipped_duplicate")

        for conversation_id, event_id in (
            ("conflict-one", "conflict-event-one"),
            ("conflict-two", "conflict-event-two"),
        ):
            self.persona.record_preference_candidate(
                "英語で答えてください。",
                conversation_id=conversation_id,
                source_event_id=event_id,
            )
        conflict_result = self.persona.apply_ready_candidates()
        self.assertEqual(conflict_result["applied"], [])
        self.assertEqual(conflict_result["skipped"][0]["status"], "skipped_conflict")

    def test_before_submit_prompt_injects_system_bootstrap(self) -> None:
        # 1. When persona is not initialized, bootstrap prompt is still injected alone
        result_without_persona = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "workspace_roots": ["C:/workspace-a"],
                "prompt": "作業を開始します",
            }
        )
        ctx_without = result_without_persona.get("additional_context") or ""
        self.assertIn("[lucid-memories bootstrap]", ctx_without)
        self.assertIn("whoami と status を一度呼べ", ctx_without)
        self.assertNotIn("[global persona]", ctx_without)

        # 2. When persona is initialized, both bootstrap and persona are injected
        self.persona.export_user_rules(self.source)
        result_with_persona = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "workspace_roots": ["C:/workspace-a"],
                "prompt": "作業を開始します",
            }
        )
        ctx_with = result_with_persona.get("additional_context") or ""
        self.assertIn("[lucid-memories bootstrap]", ctx_with)
        self.assertIn("[global persona]", ctx_with)
        self.assertIn("Always respond in Japanese.", ctx_with)


if __name__ == "__main__":
    unittest.main()
