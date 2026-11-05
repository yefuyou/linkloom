"""Beta startup switch; fake factory only, no graph/model or paid evaluation."""
from types import SimpleNamespace
import pytest
from linkloom.ui.unified_ask import UnifiedAskService


@pytest.mark.parametrize("setting", [None, "on", " ON "])
def test_default_and_explicit_on_compose_graph_with_fallback(tmp_path, monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("LINKLOOM_GRAPH_QUERY", raising=False)
    else:
        monkeypatch.setenv("LINKLOOM_GRAPH_QUERY", setting)
    app = SimpleNamespace(repository=SimpleNamespace(database_path=str(tmp_path / "app.sqlite")))
    worker = object()
    calls = []
    def factory(given_app, investigation):
        calls.append(given_app)
        return worker
    monkeypatch.setattr("linkloom.ui.graph_investigation.product_graph_service", factory)
    service = UnifiedAskService(app, lambda: None)
    assert service.graph_service is worker and calls == [app]


@pytest.mark.parametrize("setting", ["off", " OFF "])
def test_off_never_constructs_worker_or_keeps_injected_worker(tmp_path, monkeypatch, setting):
    monkeypatch.setenv("LINKLOOM_GRAPH_QUERY", setting)
    def forbidden(*args):
        raise AssertionError("off must not create a graph worker")
    monkeypatch.setattr("linkloom.ui.graph_investigation.product_graph_service", forbidden)
    app = SimpleNamespace(repository=SimpleNamespace(database_path=str(tmp_path / "app.sqlite")))
    assert UnifiedAskService(app, lambda: None).graph_service is None
    assert UnifiedAskService(app, lambda: None, graph_service=object()).graph_service is None


def test_explicit_host_disable_still_wins_when_default_is_on(tmp_path, monkeypatch):
    monkeypatch.delenv("LINKLOOM_GRAPH_QUERY", raising=False)
    app = SimpleNamespace(repository=SimpleNamespace(database_path=str(tmp_path / "app.sqlite")))
    service = UnifiedAskService(app, lambda: None, graph_service=False)
    assert service.graph_service is None
