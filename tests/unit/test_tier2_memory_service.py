"""Unit tests for B1-4: Tier 2 Persistent / Profile Memory and FeedbackMemory edit flow."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from linkloom.memory import (
    MemoryPolicyViolation,
    MemoryStatus,
    Tier2Category,
    Tier2Exposure,
    Tier2MemoryService,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui.feedback_memory import FeedbackMemory


class TestTier2MemoryService(unittest.TestCase):
    def test_preference_and_glossary_lifecycle_edit_revoke_and_restart(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = str(Path(tmp) / "memory.jsonl")
            svc = Tier2MemoryService(log_path)

            cand = svc.propose(
                "ws-1",
                category=Tier2Category.PREFERENCE,
                value={"language": "zh-CN", "summary_style": "bullet"},
                source_refs=["ui:pref-1"],
                actor="pm-1",
            )
            self.assertEqual(cand.status, MemoryStatus.PENDING)
            self.assertEqual(svc.build_prompt_context("ws-1"), [])

            # Confirm with human edit
            item = svc.confirm(
                "ws-1",
                cand.id,
                actor="pm-1",
                human_confirmed=True,
                edited_value={"language": "zh-CN", "summary_style": "concise_table"},
            )
            self.assertEqual(item.status, MemoryStatus.ACTIVE)
            self.assertEqual(item.value["summary_style"], "concise_table")

            # Edit active item -> supersedes old, activates new
            edited = svc.edit_active(
                "ws-1",
                item.id,
                actor="pm-1",
                edited_value={"language": "en-US", "summary_style": "concise_table"},
                human_confirmed=True,
            )
            self.assertEqual(edited.value["language"], "en-US")

            # Restart persistence
            restarted = Tier2MemoryService(log_path)
            prompt_ctx = restarted.build_prompt_context("ws-1", query="language summary_style")
            self.assertEqual(len(prompt_ctx), 1)
            self.assertEqual(prompt_ctx[0]["value"]["language"], "en-US")

            # Revoke survives restart
            restarted.revoke("ws-1", edited.id, actor="pm-1", human_confirmed=True)
            restarted_again = Tier2MemoryService(log_path)
            self.assertEqual(restarted_again.build_prompt_context("ws-1"), [])
            self.assertEqual(restarted_again.read_authorized("ws-1"), [])

    def test_store_read_prompt_boundaries(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = str(Path(tmp) / "memory.jsonl")
            svc = Tier2MemoryService(log_path)

            with self.assertRaises(MemoryPolicyViolation):
                svc.propose(
                    "ws-1",
                    category=Tier2Category.RESTRICTED_IDENTITY,
                    value={"employee_badge": "EMP-9981"},
                    source_refs=["hr:1"],
                    actor="admin-1",
                    exposure=Tier2Exposure.PROMPT_ALLOWED,
                )

            c_id = svc.propose(
                "ws-1",
                category=Tier2Category.RESTRICTED_IDENTITY,
                value={"employee_badge": "EMP-9981"},
                source_refs=["hr:1"],
                actor="admin-1",
            )
            svc.confirm("ws-1", c_id.id, actor="admin-1", human_confirmed=True)

            c_prof = svc.propose(
                "ws-1",
                category=Tier2Category.CONFIRMED_PROFILE,
                value={"team": "Platform Payments"},
                source_refs=["profile:1"],
                actor="admin-1",
            )
            svc.confirm("ws-1", c_prof.id, actor="admin-1", human_confirmed=True)

            c_gloss = svc.propose(
                "ws-1",
                category=Tier2Category.GLOSSARY,
                value={"term": "paygw", "meaning": "Payment Gateway"},
                source_refs=["glossary:1"],
                actor="pm-1",
            )
            svc.confirm("ws-1", c_gloss.id, actor="pm-1", human_confirmed=True)

            # Default authorized read excludes STORE_ONLY restricted identity
            default_reads = svc.read_authorized("ws-1")
            self.assertEqual({x.metadata["category"] for x in default_reads}, {"confirmed_profile", "glossary"})

            # Explicit restricted read includes STORE_ONLY
            restricted_reads = svc.read_authorized("ws-1", allow_restricted=True)
            self.assertEqual(
                {x.metadata["category"] for x in restricted_reads},
                {"restricted_identity", "confirmed_profile", "glossary"},
            )

            # Prompt context ONLY includes PROMPT_ALLOWED (glossary), never profile or restricted identity
            prompt_ctx = svc.build_prompt_context("ws-1", query="paygw team badge")
            self.assertEqual(len(prompt_ctx), 1)
            self.assertEqual(prompt_ctx[0]["value"]["term"], "paygw")

    def test_feedback_memory_approve_with_edit_and_edit_active(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = str(Path(tmp) / "feedback.jsonl")
            fb = FeedbackMemory(log_path)
            resolver = FrozenRelationResolver(
                canonical_relations=("uses_vendor",),
                aliases={"uses vendor": "uses_vendor"},
                schema_version="v1",
            )
            slots = [{"relation": "uses_vendor", "subject": "Platform", "values": ["Birchline"]}]

            created, status = fb.action(
                "propose",
                {"actor": "pm-1", "term": "adopts", "relation": "uses_vendor"},
                "ws-1",
                resolver,
                slots,
            )
            self.assertEqual(status, 201)
            cand_id = created["entry"]["id"]

            approved, status = fb.action(
                "approve",
                {
                    "actor": "pm-1",
                    "id": cand_id,
                    "human_confirmed": True,
                    "term": "adopted",
                },
                "ws-1",
                resolver,
                slots,
            )
            self.assertEqual(status, 200)
            self.assertEqual(approved["entry"]["term"], "adopted")
            item_id = approved["entry"]["id"]

            edited, status = fb.action(
                "edit",
                {
                    "actor": "pm-1",
                    "id": item_id,
                    "human_confirmed": True,
                    "term": "switched",
                },
                "ws-1",
                resolver,
                slots,
            )
            self.assertEqual(status, 200)
            self.assertEqual(edited["entry"]["term"], "switched")

            restarted = FeedbackMemory(log_path)
            combined, meta = restarted.snapshot("ws-1", "we switched to Wrenwell", resolver, slots)
            self.assertEqual(combined.aliases.get("switched"), "uses_vendor")
            self.assertEqual(len(meta["used"]), 1)
