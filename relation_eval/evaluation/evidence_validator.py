from typing import List, Dict, Any, Tuple
from relation_eval.schemas import TopicPrediction, RelationPrediction
from relation_eval.dataset.models import GoldDataset

class EvidenceValidator:
    def __init__(self, doc_contents: Dict[str, str]):
        """
        doc_contents: dict mapping note_path to the raw content string of the file
        """
        self.doc_contents = doc_contents

    def validate_topic_prediction(self, pred: TopicPrediction) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Validates topic evidence. Returns (is_valid, list of issues).
        """
        issues = []
        
        # If the agent says there is no link, evidence should be empty
        if not pred.should_link_to_agent_evaluation:
            if pred.evidence:
                issues.append({
                    "type": "EVIDENCE_UNEXPECTED",
                    "message": "Evidence provided when should_link_to_agent_evaluation is False"
                })
            return len(issues) == 0, issues

        # If it should link, check evidence is present
        if not pred.evidence:
            issues.append({
                "type": "EVIDENCE_MISSING",
                "message": "Evidence is empty but should_link_to_agent_evaluation is True"
            })
            return False, issues

        if not isinstance(pred.evidence, list):
            issues.append({
                "type": "EVIDENCE_INVALID_TYPE",
                "message": f"Evidence is not a list: {type(pred.evidence)}"
            })
            return False, issues

        # Check each evidence string is in the doc
        content = self.doc_contents.get(pred.note_path)
        if content is None:
            issues.append({
                "type": "DOCUMENT_NOT_FOUND",
                "message": f"Document content for {pred.note_path} not loaded"
            })
            return False, issues

        for ev in pred.evidence:
            if not isinstance(ev, str):
                issues.append({
                    "type": "EVIDENCE_INVALID_ELEMENT",
                    "message": f"Evidence element is not string: {type(ev)}"
                })
                continue
            if ev not in content:
                issues.append({
                    "type": "EVIDENCE_NOT_IN_SOURCE",
                    "message": f"Evidence string '{ev[:50]}...' not found in document"
                })

        return len(issues) == 0, issues

    def validate_relation_prediction(self, pred: RelationPrediction) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Validates relation evidence. Returns (is_valid, list of issues).
        """
        issues = []

        if not pred.should_link:
            # If should_link is False, source and target evidence must be empty
            if pred.evidence.source or pred.evidence.target:
                issues.append({
                    "type": "EVIDENCE_UNEXPECTED",
                    "message": "Evidence provided when should_link is False"
                })
            return len(issues) == 0, issues

        # If should_link is True, check evidence
        if not pred.evidence.source and not pred.evidence.target:
            issues.append({
                "type": "EVIDENCE_MISSING",
                "message": "Relation evidence is empty but should_link is True"
            })
            return False, issues

        # Validate source note evidence
        source_path = pred.source
        source_content = self.doc_contents.get(source_path) if source_path else None
        if source_path and source_content is None:
            issues.append({
                "type": "DOCUMENT_NOT_FOUND",
                "message": f"Source document content for {source_path} not loaded"
            })
        elif source_content:
            for ev in pred.evidence.source:
                if ev not in source_content:
                    issues.append({
                        "type": "EVIDENCE_NOT_IN_SOURCE",
                        "message": f"Source evidence string '{ev[:50]}...' not found in source document {source_path}"
                    })

        # Validate target note evidence
        target_path = pred.target
        target_content = self.doc_contents.get(target_path) if target_path else None
        if target_path and target_content is None:
            issues.append({
                "type": "DOCUMENT_NOT_FOUND",
                "message": f"Target document content for {target_path} not loaded"
            })
        elif target_content:
            for ev in pred.evidence.target:
                if ev not in target_content:
                    issues.append({
                        "type": "EVIDENCE_NOT_IN_SOURCE",
                        "message": f"Target evidence string '{ev[:50]}...' not found in target document {target_path}"
                    })

        return len(issues) == 0, issues

    def validate_gold_dataset(self, gold_dataset: GoldDataset) -> List[Dict[str, Any]]:
        """
        Validates all evidence in the golden dataset.
        Returns a list of issue dictionaries. Any issue here is a GOLD_DATA_ERROR.
        """
        issues = []
        
        # 1. Check notes
        for note in gold_dataset.notes:
            content = self.doc_contents.get(note.note_path)
            if content is None:
                issues.append({
                    "type": "GOLD_DATA_ERROR",
                    "note_path": note.note_path,
                    "message": "Document path in gold note does not exist on disk"
                })
                continue
            
            if note.should_link_to_agent_evaluation:
                if not note.evidence:
                    issues.append({
                        "type": "GOLD_DATA_ERROR",
                        "note_path": note.note_path,
                        "message": "Gold note should link but has empty evidence"
                    })
                for ev in note.evidence:
                    if ev not in content:
                        issues.append({
                            "type": "GOLD_DATA_ERROR",
                            "note_path": note.note_path,
                            "message": f"Gold note evidence '{ev[:50]}...' not found in source content"
                        })

        # 2. Check pairs
        for pair in gold_dataset.expected_pairs:
            # Check only active links
            if not pair.should_link:
                continue

            src_content = self.doc_contents.get(pair.source)
            tgt_content = self.doc_contents.get(pair.target)

            if src_content is None:
                issues.append({
                    "type": "GOLD_DATA_ERROR",
                    "pair": (pair.source, pair.target),
                    "message": f"Source document {pair.source} in gold pair does not exist on disk"
                })
            else:
                if not pair.evidence.source:
                    issues.append({
                        "type": "GOLD_DATA_ERROR",
                        "pair": (pair.source, pair.target),
                        "message": "Gold pair should link but has empty source evidence"
                    })
                for ev in pair.evidence.source:
                    if ev not in src_content:
                        issues.append({
                            "type": "GOLD_DATA_ERROR",
                            "pair": (pair.source, pair.target),
                            "message": f"Gold pair source evidence '{ev[:50]}...' not found in source content"
                        })

            if tgt_content is None:
                issues.append({
                    "type": "GOLD_DATA_ERROR",
                    "pair": (pair.source, pair.target),
                    "message": f"Target document {pair.target} in gold pair does not exist on disk"
                })
            else:
                if not pair.evidence.target:
                    issues.append({
                        "type": "GOLD_DATA_ERROR",
                        "pair": (pair.source, pair.target),
                        "message": "Gold pair should link but has empty target evidence"
                    })
                for ev in pair.evidence.target:
                    if ev not in tgt_content:
                        issues.append({
                            "type": "GOLD_DATA_ERROR",
                            "pair": (pair.source, pair.target),
                            "message": f"Gold pair target evidence '{ev[:50]}...' not found in target content"
                        })

        return issues
