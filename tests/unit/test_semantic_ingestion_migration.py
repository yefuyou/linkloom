from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3

import pytest

from linkloom.semantic_ingestion.candidate_models import candidate_subject_key_for
from linkloom.decision_memory import store as store_module
from linkloom.decision_memory.store import TemporalDecisionStore


def _create_legacy_store(path, records: tuple[tuple[str, str, str], ...]) -> None:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(store_module._SCHEMA)
    valid_from = store_module._to_storage_time(datetime(2026, 1, 1, tzinfo=UTC))
    for index, (decision_id, legacy_key, relation) in enumerate(records):
        connection.execute(
            """
            INSERT INTO decision (
                decision_id, workspace_id, subject_key, subject, relation,
                subject_normalized, relation_normalized, value, status,
                memory_state, valid_from, valid_to, supersedes_id,
                source_episode_id, provenance_run_id
            ) VALUES (?, 'ws-alpha', ?, 'launch', ?, 'launch', ?, ?, 'CURRENT',
                      'ACTIVE', ?, NULL, NULL, ?, ?)
            """,
            (
                decision_id,
                legacy_key,
                relation,
                relation.casefold(),
                f"Vendor {chr(ord('A') + index)}",
                valid_from,
                f"episode:{decision_id}",
                f"run:{decision_id}",
            ),
        )
        connection.execute(
            "INSERT INTO decision_evidence(decision_id, ordinal, evidence_ref) VALUES (?, 0, ?)",
            (decision_id, f"evidence:{decision_id}"),
        )
    connection.execute(
        """
        INSERT INTO decision_candidate (
            candidate_id, workspace_id, subject_key, subject, relation,
            subject_normalized, relation_normalized, proposed_value,
            source_evidence_refs, source_hashes, provenance_run_id, created_at, status
        ) VALUES (
            'candidate-legacy', 'ws-alpha', 'launch uses vendor', 'launch',
            'uses vendor', 'launch', 'uses vendor', 'Vendor A', ?, '{}',
            'run:candidate-legacy', ?, 'CANDIDATE'
        )
        """,
        (
            json.dumps(["evidence:decision-a"]),
            valid_from,
        ),
    )
    connection.commit()
    connection.close()


def test_unversioned_store_migrates_relation_slots_and_preserves_legacy_lookups(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite"
    _create_legacy_store(
        path,
        (
            ("decision-vendor", "launch", "uses vendor"),
            ("decision-owner", "launch owner", "owns team"),
        ),
    )

    with TemporalDecisionStore(path) as store:
        assert store.schema_version == 2
        assert store.get_current("ws-alpha", "launch").decision_id == "decision-vendor"
        assert store.get_current("ws-alpha", "launch owner").decision_id == "decision-owner"
        vendor = store.get_decision("ws-alpha", "decision-vendor")
        owner = store.get_decision("ws-alpha", "decision-owner")
        assert vendor.subject_key == candidate_subject_key_for("launch", "uses vendor")
        assert owner.subject_key == candidate_subject_key_for("launch", "owns team")
        candidate = store._connection.execute(
            "SELECT subject_key FROM decision_candidate WHERE candidate_id = 'candidate-legacy'"
        ).fetchone()
        assert candidate["subject_key"] == candidate_subject_key_for("launch", "uses vendor")
        assert [row["name"] for row in store._connection.execute(
            "PRAGMA table_info('semantic_candidate')"
        )]  # the additive workflow schema exists

    with TemporalDecisionStore(path) as reopened:
        assert reopened.schema_version == 2
        assert reopened.get_current("ws-alpha", "launch").decision_id == "decision-vendor"


def test_relation_slot_migration_rolls_back_on_active_slot_collision(tmp_path) -> None:
    path = tmp_path / "colliding-legacy.sqlite"
    _create_legacy_store(
        path,
        (
            ("decision-one", "launch-vendor", "uses vendor"),
            ("decision-two", "launch supplier", "USES VENDOR"),
        ),
    )

    with pytest.raises(RuntimeError, match="relation-scoped slot collision"):
        TemporalDecisionStore(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT subject_key FROM decision ORDER BY decision_id"
        ).fetchall() == [("launch-vendor",), ("launch supplier",)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_candidate'"
        ).fetchone() is None
    finally:
        connection.close()


def test_v1_candidate_rows_default_to_source_ingestion_when_origin_column_is_added(tmp_path) -> None:
    path = tmp_path / "v1-candidate.sqlite"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(store_module._SCHEMA)
    for statement in store_module._MIGRATION_1_SQL:
        connection.execute(statement)
    created_at = store_module._to_storage_time(datetime(2026, 10, 7, tzinfo=UTC))
    fingerprint = "a" * 64
    connection.execute(
        """
        INSERT INTO semantic_candidate (
            candidate_id, workspace_id, artifact_id, artifact_version_id,
            content_sha256, extraction_fingerprint, candidate_fingerprint,
            payload_version, validation_state, validation_reasons_json,
            workflow_state, payload_json, created_at, updated_at
        ) VALUES (
            'candidate-legacy', 'ws-alpha', 'artifact-legacy', 'version-legacy',
            ?, ?, ?, 'candidate-decision-fact/v1', 'ACCEPTED', '[]',
            'PENDING_REVIEW', '{}', ?, ?
        )
        """,
        ("b" * 64, "c" * 64, fingerprint, created_at, created_at),
    )
    connection.execute(
        """
        INSERT INTO semantic_candidate_event (
            event_id, workspace_id, candidate_id, event_type, actor_type, actor_id,
            event_at, candidate_fingerprint, reason_code, reason, metadata_json
        ) VALUES (
            'event-legacy', 'ws-alpha', 'candidate-legacy', 'CANDIDATE_CAPTURED',
            'SYSTEM', 'semantic-ingestion', ?, ?, NULL, 'legacy capture', '{}'
        )
        """,
        (created_at, fingerprint),
    )
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    with TemporalDecisionStore(path) as migrated:
        row = migrated.get_semantic_candidate_row("ws-alpha", "candidate-legacy")
        events = migrated.get_semantic_candidate_events("ws-alpha", "candidate-legacy")
        assert migrated.schema_version == 2
        assert row is not None
        assert row["candidate_source_kind"] == "SOURCE_INGESTION"
        assert events[0]["candidate_source_kind"] == "SOURCE_INGESTION"
