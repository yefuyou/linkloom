import os
import sys
import json
import argparse
import datetime
from pathlib import Path
from pydantic import ValidationError

from relation_eval.config import EvalConfig
from relation_eval.dataset.loader import DatasetLoader
from relation_eval.providers.mock import MockProvider
from relation_eval.agents.topic_classifier import TopicClassifier
from relation_eval.agents.relation_classifier import RelationClassifier
from relation_eval.evaluation.topic_scorer import TopicScorer
from relation_eval.evaluation.relation_scorer import RelationScorer
from relation_eval.evaluation.evidence_validator import EvidenceValidator
from relation_eval.evaluation.bad_case_analyzer import BadCaseAnalyzer
from relation_eval.reporting.markdown_report import MarkdownReportGenerator
from relation_eval.schemas import TopicPrediction, RelationPrediction

def main():
    parser = argparse.ArgumentParser(description="Relation Vault Agent Evaluation Runner")
    parser.add_argument("--config", default="configs/eval.mock.yaml", help="Path to YAML configuration file")
    parser.add_argument("--dataset", help="Override dataset name")
    parser.add_argument("--provider", help="Override provider type (mock, etc.)")
    parser.add_argument("--mock-scenario", help="Override mock scenario (perfect, noisy, malformed)")

    args = parser.parse_args()

    # 1. Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    config = EvalConfig.load_from_yaml(config_path)

    # Apply overrides
    if args.dataset:
        config.dataset.name = args.dataset
    if args.provider:
        config.provider.type = args.provider
    if args.mock_scenario:
        config.provider.scenario = args.mock_scenario

    print(f"Starting evaluation run: {config.dataset.name}")
    print(f"Provider: {config.provider.type} (Scenario: {config.provider.scenario})")

    started_at = datetime.datetime.utcnow()

    # 2. Loader
    loader = DatasetLoader(config.dataset.fixture_path, config.dataset.documents_root)
    gold_dataset = loader.load_gold_dataset()
    documents = loader.load_documents()

    print(f"Loaded {len(documents)} documents from {config.dataset.documents_root}")
    
    # 3. Gold check
    doc_contents = {d.note_path: d.content for d in documents}
    validator = EvidenceValidator(doc_contents)
    gold_issues = validator.validate_gold_dataset(gold_dataset)
    if gold_issues:
        print(f"WARNING: Found {len(gold_issues)} issues in Golden Dataset (GOLD_DATA_ERROR)")

    # 4. Provider & Classifiers
    if config.provider.type == "openai":
        from relation_eval.providers.openai import OpenAIProvider
        import os
        api_key = config.provider.api_key or os.environ.get("OPENAI_API_KEY")
        base_url = config.provider.base_url or os.environ.get("OPENAI_BASE_URL")
        if not api_key:
            raise ValueError("API key must be configured in configs/eval.mock.yaml or via OPENAI_API_KEY env variable.")
        provider = OpenAIProvider(api_key=api_key, base_url=base_url, model=config.provider.model)
    else:
        provider = MockProvider(gold_dataset, scenario=config.provider.scenario)

    topic_classifier = TopicClassifier(provider)
    relation_classifier = RelationClassifier(provider)

    bad_case_analyzer = BadCaseAnalyzer()
    if gold_issues:
        bad_case_analyzer.add_gold_errors(gold_issues)

    # 5. Run inference
    topic_predictions = []
    malformed_topics = []
    
    if config.inference.topic_enabled:
        print("Running topic classification...")
        for doc in documents:
            try:
                # MockProvider might raise ValidationError in malformed scenario
                pred = topic_classifier.classify(doc)
                topic_predictions.append(pred)
            except ValidationError as ve:
                print(f"  Schema validation error on document {doc.note_path}: {ve}")
                bad_case_analyzer.add_schema_error([doc.note_path], str(ve))
                malformed_topics.append(doc.note_path)
            except Exception as e:
                print(f"  Provider error on document {doc.note_path}: {e}")
                bad_case_analyzer.add_provider_error([doc.note_path], str(e))
                malformed_topics.append(doc.note_path)

    relation_predictions = []
    malformed_relations = []
    candidate_pairs = []

    for i in range(len(documents)):
        for j in range(i + 1, len(documents)):
            candidate_pairs.append((documents[i], documents[j]))

    if config.inference.relation_enabled:
        print(f"Running relation classification on {len(candidate_pairs)} candidate pairs...")
        for left, right in candidate_pairs:
            try:
                pred = relation_classifier.classify(left, right)
                relation_predictions.append(pred)
            except ValidationError as ve:
                print(f"  Schema validation error on pair {left.note_path} - {right.note_path}: {ve}")
                bad_case_analyzer.add_schema_error([left.note_path, right.note_path], str(ve))
                malformed_relations.append((left.note_path, right.note_path))
            except Exception as e:
                print(f"  Provider error on pair {left.note_path} - {right.note_path}: {e}")
                bad_case_analyzer.add_provider_error([left.note_path, right.note_path], str(e))
                malformed_relations.append((left.note_path, right.note_path))

    # 6. Evidence validation
    topic_evidence_issues = {}
    valid_topic_predictions = []
    for pred in topic_predictions:
        is_ok, issues = validator.validate_topic_prediction(pred)
        if not is_ok:
            topic_evidence_issues[pred.note_path] = issues
        valid_topic_predictions.append(pred)

    relation_evidence_issues = {}
    valid_relation_predictions = []
    for pred in relation_predictions:
        is_ok, issues = validator.validate_relation_prediction(pred)
        if not is_ok:
            relation_evidence_issues[(pred.left_note_path, pred.right_note_path)] = issues
        valid_relation_predictions.append(pred)

    # 7. Scorers
    topic_scorer = TopicScorer(gold_dataset)
    topic_results = topic_scorer.score(valid_topic_predictions)

    relation_scorer = RelationScorer(gold_dataset)
    relation_results = relation_scorer.score(valid_relation_predictions)

    # Feed scorers details into bad case analyzer
    bad_case_analyzer.analyze_topic(topic_results["details"], topic_evidence_issues)
    bad_case_analyzer.analyze_relations(relation_results["details"], relation_evidence_issues)

    # 8. Compute final stats
    finished_at = datetime.datetime.utcnow()
    total_predictions = len(topic_predictions) + len(relation_predictions)
    invalid_evidence_count = len(topic_evidence_issues) + len(relation_evidence_issues)
    
    valid_evidence_rate = 1.0
    if total_predictions > 0:
        valid_evidence_rate = (total_predictions - invalid_evidence_count) / total_predictions

    prediction_evidence_stats = {
        "valid_rate": valid_evidence_rate,
        "invalid_count": invalid_evidence_count,
        "invalid_details": []
    }
    
    for path, issues in topic_evidence_issues.items():
        for issue in issues:
            prediction_evidence_stats["invalid_details"].append({
                "note_path": path,
                "message": issue["message"]
            })
    for pair_key, issues in relation_evidence_issues.items():
        for issue in issues:
            prediction_evidence_stats["invalid_details"].append({
                "pair": pair_key,
                "message": issue["message"]
            })

    bad_cases_list = bad_case_analyzer.get_bad_cases()

    # 9. Outputs Setup
    output_dir = Path(config.output.root)
    timestamp_str = started_at.strftime("%Y-%m-%dT%H%M%SZ")
    scenario_suffix = config.provider.scenario if config.provider.type == "mock" else config.provider.model
    run_output_dir = output_dir / f"{timestamp_str}_{config.provider.type}_{scenario_suffix}"
    run_output_dir.mkdir(parents=True, exist_ok=True)

    request_count = getattr(provider, "request_count", 0)
    input_tokens = getattr(provider, "input_tokens", 0)
    output_tokens = getattr(provider, "output_tokens", 0)
    
    estimated_cost_usd = 0.0
    if config.provider.type == "openai":
        estimated_cost_usd = (input_tokens * 0.15 / 1000000.0) + (output_tokens * 0.60 / 1000000.0)

    run_manifest = {
        "run_id": f"run_{timestamp_str}",
        "dataset_version": gold_dataset.dataset_version,
        "provider": config.provider.type,
        "mock_scenario": config.provider.scenario if config.provider.type == "mock" else None,
        "topic_prompt_version": "topic_v1",
        "relation_prompt_version": "relation_v1",
        "started_at": started_at.isoformat() + "Z",
        "finished_at": finished_at.isoformat() + "Z",
        "document_count": len(documents),
        "candidate_pair_count": len(candidate_pairs),
        "request_count": request_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd
    }

    # Write predictions files
    with open(run_output_dir / "topic_predictions.jsonl", "w", encoding="utf-8") as f:
        for pred in topic_predictions:
            f.write(pred.model_dump_json() + "\n")

    with open(run_output_dir / "relation_predictions.jsonl", "w", encoding="utf-8") as f:
        for pred in relation_predictions:
            f.write(pred.model_dump_json() + "\n")

    # Write run_manifest.json
    with open(run_output_dir / "run_manifest.json", "w", encoding="utf-8") as f:
        json.dump(run_manifest, f, indent=2, ensure_ascii=False)

    # Write metrics.json
    metrics_out = {
        "topic_classification": {
            "counts": topic_results["counts"],
            "metrics": topic_results["metrics"]
        },
        "relation_classification": {
            "counts": relation_results["counts"],
            "metrics": relation_results["metrics"],
            "type_stats": relation_results["type_stats"]
        },
        "evidence_stats": {
            "valid_rate": valid_evidence_rate,
            "invalid_count": invalid_evidence_count
        }
    }
    with open(run_output_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics_out, f, indent=2, ensure_ascii=False)

    # Write bad_cases.json
    with open(run_output_dir / "bad_cases.json", "w", encoding="utf-8") as f:
        json.dump(bad_cases_list, f, indent=2, ensure_ascii=False)

    # Write report.md
    report_gen = MarkdownReportGenerator(
        run_metadata=run_manifest,
        topic_results=topic_results,
        relation_results=relation_results,
        gold_validation_issues=gold_issues,
        prediction_evidence_stats=prediction_evidence_stats,
        bad_cases=bad_cases_list
    )
    report_md = report_gen.generate()
    with open(run_output_dir / "report.md", "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\nEvaluation complete! Report written to {run_output_dir / 'report.md'}")
    print(f"Total Bad Cases found: {len(bad_cases_list)}")

if __name__ == "__main__":
    main()
