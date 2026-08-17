import json
from pathlib import Path
from unittest.mock import patch

from linkloom.evaluation.runner import FormalEvaluationRunner
from linkloom.evaluation.real_execution import RealProviderRunner
from linkloom.evaluation.dataset import InferenceDataset
from linkloom.evaluation.real_provider import AgyCliAdapter, ProviderResponse, ProviderUsage, InferenceRequest
from linkloom.evaluation.models import PredictionRecord

class MockRealRunner(RealProviderRunner):
    def __init__(self, adapter: AgyCliAdapter, mode: str = "perfect"):
        super().__init__(adapter)
        self.mode = mode

    def run_inference(self, dataset: InferenceDataset, run_id=None):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from tests.unit.test_evaluation_real_runner import FakeAdapter
        self.adapter = FakeAdapter(self.mode)
        return super().run_inference(dataset, run_id=run_id)


def test_real_provider_baseline_eligible(tmp_path):
    repo_root = Path(__file__).parent.parent.parent
    runner = FormalEvaluationRunner(repo_root=repo_root, output_root=tmp_path)
    
    with patch("linkloom.evaluation.real_execution.RealProviderRunner") as MockRunnerClass:
        MockRunnerClass.side_effect = lambda adapter: MockRealRunner(adapter, mode="perfect")
        
        result = runner.run(provider="gemini_cli", scenario="perfect")
    
    assert result["manifest"]["provider"] == "gemini_cli"
    assert result["manifest"]["baseline_eligible"] is True
    assert result["manifest"]["oracle"] is False
    assert result["manifest"]["status"] == "completed"
    assert result["safety"]["passed"] is True
    
    # Check that usage and evidence validations were written
    run_dir = Path(result["run_dir"])
    assert (run_dir / "usage.json").exists()
    assert (run_dir / "evidence_validation.json").exists()
    
    usage = json.loads((run_dir / "usage.json").read_text())
    assert usage["request_count"] > 2

def test_real_provider_gold_read_ordering(tmp_path):
    repo_root = Path(__file__).parent.parent.parent
    runner = FormalEvaluationRunner(repo_root=repo_root, output_root=tmp_path)
    
    call_order = []
    
    def mock_run_inference(self, dataset, run_id=None):
        call_order.append("inference")
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from tests.unit.test_evaluation_real_runner import FakeAdapter
        self.adapter = FakeAdapter("perfect")
        return super(MockRealRunner, self).run_inference(dataset, run_id=run_id)
        
    with patch.object(MockRealRunner, "run_inference", new=mock_run_inference), \
         patch("linkloom.evaluation.real_execution.RealProviderRunner") as MockRunnerClass, \
         patch("linkloom.evaluation.dataset.EvaluationDataset.get_gold_pairs") as mock_get_gold:
    
        MockRunnerClass.side_effect = lambda adapter: MockRealRunner(adapter, mode="perfect")
        
        def spy_get_gold(*args, **kwargs):
            call_order.append("gold_read")
            return []
        mock_get_gold.side_effect = spy_get_gold
        
        runner.run(provider="gemini_cli", scenario="perfect")
        
    assert call_order == ["inference", "gold_read"]
    
def test_real_provider_error_not_eligible(tmp_path):
    repo_root = Path(__file__).parent.parent.parent
    runner = FormalEvaluationRunner(repo_root=repo_root, output_root=tmp_path)
    
    with patch("linkloom.evaluation.real_execution.RealProviderRunner") as MockRunnerClass:
        MockRunnerClass.side_effect = lambda adapter: MockRealRunner(adapter, mode="error")
        
        result = runner.run(provider="gemini_cli", scenario="perfect")
    
    assert result["manifest"]["provider"] == "gemini_cli"
    assert result["manifest"]["baseline_eligible"] is False
    assert result["safety"]["passed"] is True
    assert result["manifest"]["status"] == "failed"

def test_real_provider_malformed_not_eligible(tmp_path):
    repo_root = Path(__file__).parent.parent.parent
    runner = FormalEvaluationRunner(repo_root=repo_root, output_root=tmp_path)
    
    with patch("linkloom.evaluation.real_execution.RealProviderRunner") as MockRunnerClass:
        MockRunnerClass.side_effect = lambda adapter: MockRealRunner(adapter, mode="malformed")
        
        result = runner.run(provider="gemini_cli", scenario="perfect")
    
    assert result["manifest"]["provider"] == "gemini_cli"
    assert result["manifest"]["baseline_eligible"] is False
