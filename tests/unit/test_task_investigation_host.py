from datetime import UTC, datetime

import pytest

from linkloom.ui.task_investigation import ProductTaskSources, TaskInvestigationModels
from linkloom.semantic_ingestion.artifacts import RawArtifact


def test_repository_identity_is_bound_to_raw_artifact_and_rechecked():
    source = dict(source_id='src_' + 'a' * 32, source_version='v1', name='minutes.md',
                  content='[2029-06-01T09:00:00Z] 林枫: 完成索引迁移。',
                  created_at=datetime(2029, 6, 1, tzinfo=UTC).isoformat())
    source['source_version'] = RawArtifact('workspace', source['source_id'], 'timestamped_text', source['content']).artifact_version_id
    current = [source]
    snapshot = ProductTaskSources('workspace', (source,), lambda: tuple(current))
    artifact, = snapshot.artifacts
    assert artifact.artifact_id == source['source_id']
    assert snapshot.mapping[artifact.artifact_version_id]['source_version'] == source['source_version']
    snapshot.validate_current()
    current[0] = dict(source, source_version='v2')
    with pytest.raises(ValueError, match='SOURCE_SNAPSHOT_CHANGED'):
        snapshot.validate_current()


def test_live_task_stage_configuration_is_rejected():
    with pytest.raises(ValueError, match='FAKE/REPLAY'):
        TaskInvestigationModels(None, None, None, execution_kind='LIVE')
