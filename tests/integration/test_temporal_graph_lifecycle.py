"""Actual human merge/undo through existing review services, dev sources only."""
from datetime import timedelta
from tests.unit.test_task_legacy_adapter import app
from tests.unit.test_task_merge import setup,prepare,apply,undo
from tests.unit.test_task_rebind import NOW
from linkloom.temporal_graph.store import SQLiteGraphStore


def test_only_human_merge_creates_identity_edge_and_undo_removes_it(app):
    initial,artifact,identifier=setup(app)
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *a:True)
    assert not any(e.edge_type=='same_task' for e in store.load_edges(app.workspace).edges)
    value=prepare(app,initial['task']['id'],identifier)
    view=apply(app,value)
    snapshot=store.load_edges(app.workspace)
    identity=[e for e in snapshot.edges if e.edge_type=='same_task']
    assert len(identity)==1,snapshot.exclusions
    assert identity[0].target.key==initial['task']['id']
    assert identity[0].source.key==identifier
    assert identity[0].time_basis=='HUMAN_IDENTITY_ASSERTION'
    assert all(e.edge_type!='owner_of' for e in snapshot.edges)
    undone=undo(app,view)
    assert not any(e.edge_type=='same_task' for e in store.load_edges(app.workspace,(NOW+timedelta(days=5)).isoformat()).edges)
    historical=store.load_edges(app.workspace,(NOW+timedelta(days=2)).isoformat())
    same=[e for e in historical.edges if e.edge_type=='same_task']
    assert len(same)==1 and same[0].valid_to==(NOW+timedelta(days=4)).isoformat()
