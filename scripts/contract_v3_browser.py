"""Serve one explicitly offline, synthetic Final-correction browser demonstration."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/integration"), str(ROOT)]


def main():
    from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
    from run_bounded_multihop import SourceOnlyController
    from unified_investigation_fixture import UnifiedInvestigationFixture, RELATIONS, TWO
    from scripts.adaptive_evaluation import _preserve_run_artifacts

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    fixture = UnifiedInvestigationFixture(root / "synthetic.sqlite")
    fixture.configure_agent()
    fixture.select_workspace(RELATIONS)
    controller, saved = None, []

    def respond(request):
        nonlocal controller
        if controller is None:
            controller = SourceOnlyController()
        with (root / "model-requests.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(request.to_dict(), ensure_ascii=False) + "\n")
        if saved:
            return saved[0]
        try:
            action = controller(request)
        except Exception:
            import traceback
            (root / "controller-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise
        if action.kind == "final":
            saved.append(action)
            return ModelAction.final("{truncated")
        return action

    model = FakeModelAdapter([respond] * 12)
    fixture.service.model_factory = lambda query: model
    ready = {"url": fixture.base_url, "query": TWO, "model_kind": "OFFLINE_FAKE_MODEL",
             "workspace_id": fixture.app._workspace_id(), "real_provider_requests": 0}
    (root / "ready.json").write_text(json.dumps(ready, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(ready, ensure_ascii=False), flush=True)
    captured = False
    try:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline and not (root / "stop").exists():
            for run_id in list(fixture.server.unified_ask._runs):
                if captured:
                    break
                result, _ = fixture.server.unified_ask.inspect(run_id, ready["workspace_id"])
                if result["status"] != "INVESTIGATING":
                    packet = {"model_kind": "OFFLINE_FAKE_MODEL", "result": result,
                              "model_calls": model.call_count, "real_provider_requests": 0}
                    (root / "final.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
                    _preserve_run_artifacts(root, fixture.service, run_id,
                                            {"query": TWO, "workspace_id": ready["workspace_id"]}, None, result)
                    captured = True
            time.sleep(.5)
    finally:
        fixture.stop()


if __name__ == "__main__":
    main()
