"""Host composition contracts: first use and shared trusted relation paths."""
from pathlib import Path
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.server import create_http_server
from tests.integration.test_product_application_flow import _UnusedRunBackend


def test_empty_product_can_start_with_local_review_opt_in(tmp_path):
    app=ProductApplication(database_path=tmp_path/'empty.sqlite',extractor=None)
    try:
        server=create_http_server(_UnusedRunBackend(),product_app=app,allow_local_task_review=True,port=0)
        try:
            assert app.repository.current_workspace_id() is None
            assert not (tmp_path/'empty.sqlite.relations.sqlite').exists()
        finally:server.server_close()
    finally:app.close()


def test_default_relation_path_is_shared_with_runtime_and_explicit_path_wins(tmp_path):
    app=ProductApplication(database_path=tmp_path/'product.sqlite',extractor=None)
    ws=app.repository.create_workspace('Synthetic host composition')['workspace_id']
    app.repository.select_workspace(ws)
    service=ProductInvestigationService(app)
    server=create_http_server(_UnusedRunBackend(),product_app=app,investigation_service=service,allow_local_task_review=True,port=0)
    try:
        expected=tmp_path/'product.sqlite.relations.sqlite'
        assert service.task_relation_paths[ws]==expected
        assert server._task_relation_sidecar_for(ws)==expected
        other=app.repository.create_workspace('Other synthetic workspace')['workspace_id']
        trusted=tmp_path/'explicit-trusted.sqlite'
        service.task_relation_paths[other]=trusted
        server.ensure_task_relation_sidecar(other)
        assert server._task_relation_sidecar_for(other)==trusted
        assert not expected.exists() and not trusted.exists()
    finally:server.server_close();app.close()
