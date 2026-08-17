from .models import (
    InferenceDocument,
    InferencePair,
    PredictionRecord,
    DatasetManifest,
    MetricResult,
    BadCase,
    TraceQuality,
    SafetyReport,
    EvaluationRunManifest,
    GoldNote,
    GoldPair
)
from .dataset import EvaluationDataset, InferenceDataset
from .metrics import (
    binary_metrics,
    topic_metrics,
    relation_metrics,
    relation_metrics_from_records,
    evidence_metrics,
    evidence_ownership_metrics,
    retrieval_hit_at_k,
    retrieval_mrr,
    trace_quality,
    trace_quality_metrics,
)
from .isolation import check_inference_payload, check_inference_module, snapshot_tree, compare_snapshots
from .bad_cases import BadCaseCategory, BadCaseSeverity, create_bad_case, classify_link_case, classify_relation_detail
from .runner import FormalEvaluationRunner, evaluate
