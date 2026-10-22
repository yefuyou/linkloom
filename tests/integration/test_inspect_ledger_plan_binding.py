"""Cold preparation must bind the whole plan to the canonical Host context."""
from copy import deepcopy
from dataclasses import replace

import pytest

from linkloom.ui.task_investigation import ProductTaskSources, prepare_task_investigation, restore_task_preparation
from tests.integration.test_inspect_ledger_dependency import InspectThenRelationPlanner
from tests.m3p_support import QUESTION, revision_product


@pytest.mark.parametrize('metadata', ['workspace_id', 'snapshot_hash'])
def test_cold_preparation_rejects_self_consistent_foreign_plan(tmp_path, metadata):
    app, service, _, workspace, *_ = revision_product(tmp_path)
    try:
        sources = ProductTaskSources(workspace, app.snapshot_investigation_sources(workspace)[1],
            lambda: app.snapshot_investigation_sources(workspace)[1])
        prepared = prepare_task_investigation(QUESTION, sources,
            replace(service.task_models, planner=InspectThenRelationPlanner()), tmp_path / 'preparation',
            relation_path=tmp_path / 'relations.sqlite')
        assert restore_task_preparation(sources, prepared.manifest).tools.inspection_plan == prepared.tools.inspection_plan
        manifest = deepcopy(prepared.manifest)
        foreign = replace(prepared.tools.inspection_plan,
            **{metadata: 'foreign-workspace' if metadata == 'workspace_id' else '0' * 64})
        manifest['planning'][-1]['plan'] = foreign.to_dict()
        with pytest.raises(ValueError):
            restore_task_preparation(sources, manifest)
    finally:
        app.close()
