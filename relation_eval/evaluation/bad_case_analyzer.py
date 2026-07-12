from typing import List, Dict, Any, Tuple

class BadCaseAnalyzer:
    def __init__(self):
        self.bad_cases = []

    def analyze_topic(self, topic_details: List[Dict[str, Any]], evidence_issues: Dict[str, List[Dict[str, Any]]]) -> None:
        """
        topic_details: details list from TopicScorer
        evidence_issues: mapping from note_path to list of evidence validation issues
        """
        for doc in topic_details:
            path = doc["note_path"]
            
            # 1. Classification errors
            if doc["bad_case_type"]:
                self.bad_cases.append({
                    "case_type": doc["bad_case_type"],
                    "note_paths": [path],
                    "gold": {
                        "should_link_to_agent_evaluation": doc["gold_label"]
                    },
                    "prediction": {
                        "should_link_to_agent_evaluation": doc["predicted_label"],
                        "confidence": doc["confidence"],
                        "evidence": doc["predicted_evidence"]
                    },
                    "summary": f"Topic classification error: Gold is {doc['gold_label']}, Prediction is {doc['predicted_label']}."
                })

            # 2. Evidence validation issues
            issues = evidence_issues.get(path, [])
            for issue in issues:
                # Map validation issue to case type
                case_type = "EVIDENCE_NOT_IN_SOURCE"
                if issue["type"] == "EVIDENCE_UNEXPECTED":
                    case_type = "EVIDENCE_SOURCE_TARGET_MISMATCH"
                
                self.bad_cases.append({
                    "case_type": case_type,
                    "note_paths": [path],
                    "gold": {},
                    "prediction": {
                        "evidence": doc["predicted_evidence"]
                    },
                    "summary": f"Evidence validation failed: {issue['message']}"
                })

    def analyze_relations(self, relation_details: List[Dict[str, Any]], evidence_issues: Dict[Tuple, List[Dict[str, Any]]]) -> None:
        """
        relation_details: details list from RelationScorer
        evidence_issues: mapping from (left, right) tuple to list of evidence validation issues
        """
        for pair in relation_details:
            path_a = pair["left_note_path"]
            path_b = pair["right_note_path"]
            key = (path_a, path_b)
            
            # 1. Link/type/direction classification errors
            if pair["bad_case_type"]:
                gold_summary = {
                    "should_link": pair["gold_link"],
                    "source": pair["gold_source"],
                    "target": pair["gold_target"],
                    "relation_type": pair["gold_type"]
                }
                pred_summary = {
                    "should_link": pair["predicted_link"],
                    "source": pair["predicted_source"],
                    "target": pair["predicted_target"],
                    "relation_type": pair["predicted_type"],
                    "confidence": pair["confidence"]
                }

                if pair["bad_case_type"] == "RELATION_FALSE_POSITIVE":
                    summary = "Relation False Positive: Predicted relationship that should not exist."
                elif pair["bad_case_type"] == "RELATION_FALSE_NEGATIVE":
                    summary = "Relation False Negative: Missed expected relationship."
                elif pair["bad_case_type"] == "RELATION_DIRECTION_ERROR":
                    summary = f"Relation direction incorrect: Expected {pair['gold_source']} -> {pair['gold_target']}, got {pair['predicted_source']} -> {pair['predicted_target']}."
                elif pair["bad_case_type"] == "RELATION_TYPE_ERROR":
                    summary = f"Relation type incorrect: Expected {pair['gold_type']}, got {pair['predicted_type']}."
                else:
                    summary = "Relation error."

                self.bad_cases.append({
                    "case_type": pair["bad_case_type"],
                    "note_paths": [path_a, path_b],
                    "gold": gold_summary,
                    "prediction": pred_summary,
                    "summary": summary
                })

            # 2. Evidence validation issues
            issues = evidence_issues.get(key, [])
            if not issues:
                # try swapped key
                issues = evidence_issues.get((path_b, path_a), [])

            for issue in issues:
                case_type = "EVIDENCE_NOT_IN_SOURCE"
                if issue["type"] == "EVIDENCE_UNEXPECTED":
                    case_type = "EVIDENCE_SOURCE_TARGET_MISMATCH"
                
                self.bad_cases.append({
                    "case_type": case_type,
                    "note_paths": [path_a, path_b],
                    "gold": {},
                    "prediction": {
                        "source": pair["predicted_source"],
                        "target": pair["predicted_target"],
                        "evidence_source": pair.get("predicted_evidence_source", []),
                        "evidence_target": pair.get("predicted_evidence_target", [])
                    },
                    "summary": f"Relation evidence validation failed: {issue['message']}"
                })

    def add_schema_error(self, path_or_pair: List[str], error_message: str) -> None:
        self.bad_cases.append({
            "case_type": "INVALID_PREDICTION_SCHEMA",
            "note_paths": path_or_pair,
            "gold": {},
            "prediction": {},
            "summary": f"Failed Pydantic schema validation: {error_message}"
        })

    def add_provider_error(self, path_or_pair: List[str], error_message: str) -> None:
        self.bad_cases.append({
            "case_type": "PROVIDER_ERROR",
            "note_paths": path_or_pair,
            "gold": {},
            "prediction": {},
            "summary": f"Provider failed execution: {error_message}"
        })

    def add_gold_errors(self, gold_issues: List[Dict[str, Any]]) -> None:
        for issue in gold_issues:
            paths = []
            if "note_path" in issue:
                paths = [issue["note_path"]]
            elif "pair" in issue:
                paths = list(issue["pair"])
            
            self.bad_cases.append({
                "case_type": "GOLD_DATA_ERROR",
                "note_paths": paths,
                "gold": {},
                "prediction": {},
                "summary": f"Golden Dataset issue: {issue['message']}"
            })

    def get_bad_cases(self) -> List[Dict[str, Any]]:
        return self.bad_cases
