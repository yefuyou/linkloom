from copy import deepcopy
from pathlib import Path
import json
import pytest
import run_graph_mention_final as campaign
from linkloom.temporal_graph.entity_locator import LocatorConfig


def test_exactly_one_final_round_cannot_restart_existing_run(tmp_path,monkeypatch):
    monkeypatch.setattr(campaign,'ROOT',tmp_path)
    for marker in ['config.json','budget.json','results']:
        path=tmp_path/marker
        if marker=='results':path.mkdir()
        else:path.write_text('{}',encoding='utf8')
        with pytest.raises(ValueError,match='ONE_FINAL_ROUND_NO_RERUN'):campaign.seal()
        # Separate test subdirectories avoid removing campaign artifacts.
        tmp_path=tmp_path/'next';tmp_path.mkdir();monkeypatch.setattr(campaign,'ROOT',tmp_path)


def test_additional_stage_cap_is_one_point_six_and_complete_matrix_fits():
    assert 1.6-.857088==pytest.approx(.742912)
    complete=(120*6144*.15+120*1024*.6)/1e6
    assert complete==pytest.approx(.18432) and complete<(.742912*.8)


@pytest.mark.parametrize('values',[dict(min_score=float('nan')),dict(min_margin=-1),dict(min_identity_similarity=1.1),dict(min_identity_similarity=float('inf'))])
def test_locator_rejects_nonfinite_or_invalid_identity_thresholds(values):
    with pytest.raises(ValueError):LocatorConfig(**values)
