from typing import List, Dict, Any, Tuple
from relation_eval.schemas import RelationPrediction
from relation_eval.dataset.models import GoldDataset

class RelationScorer:
    def __init__(self, gold_dataset: GoldDataset):
        self.gold_dataset = gold_dataset
        
        # Build lookup maps for gold pairs. Key is unordered tuple of paths.
        self.gold_map = {}
        for p in gold_dataset.expected_pairs:
            key = tuple(sorted([p.source, p.target]))
            self.gold_map[key] = p

    def score(self, predictions: List[RelationPrediction]) -> Dict[str, Any]:
        tp = 0
        tn = 0
        fp = 0
        fn = 0

        correct_direction = 0
        correct_type = 0
        linkage_tps = 0

        type_stats = {
            "concept-to-practice": {"correct": 0, "incorrect": 0},
            "concept-to-interview": {"correct": 0, "incorrect": 0},
            "practice-to-interview": {"correct": 0, "incorrect": 0}
        }

        details = []

        for pred in predictions:
            # Unordered key to look up gold pair
            key = tuple(sorted([pred.left_note_path, pred.right_note_path]))
            gold = self.gold_map.get(key)

            # Determine gold status
            gold_pos = False
            if gold and gold.should_link:
                gold_pos = True

            pred_pos = pred.should_link

            is_link_correct = (gold_pos == pred_pos)
            bad_case_type = None

            direction_correct = None
            type_correct = None

            if not is_link_correct:
                if pred_pos:
                    fp += 1
                    bad_case_type = "RELATION_FALSE_POSITIVE"
                else:
                    fn += 1
                    bad_case_type = "RELATION_FALSE_NEGATIVE"
            else:
                if pred_pos:
                    tp += 1
                    linkage_tps += 1
                    
                    # Both agree there is a link. Check direction and type.
                    # Direction check: we compare predicted source and target with gold
                    direction_ok = (pred.source == gold.source and pred.target == gold.target)
                    if direction_ok:
                        correct_direction += 1
                        direction_correct = True
                    else:
                        direction_correct = False
                        bad_case_type = "RELATION_DIRECTION_ERROR"

                    # Type check:
                    type_ok = (pred.relation_type == gold.relation_type)
                    if type_ok:
                        correct_type += 1
                        type_correct = True
                        if gold.relation_type in type_stats:
                            type_stats[gold.relation_type]["correct"] += 1
                    else:
                        type_correct = False
                        if not bad_case_type:  # Prefer direction error over type error or record both? 
                            # Let's assign bad_case_type to RELATION_TYPE_ERROR if direction was correct
                            bad_case_type = "RELATION_TYPE_ERROR"
                        if gold.relation_type in type_stats:
                            type_stats[gold.relation_type]["incorrect"] += 1
                else:
                    tn += 1

            details.append({
                "left_note_path": pred.left_note_path,
                "right_note_path": pred.right_note_path,
                "gold_link": gold_pos,
                "predicted_link": pred_pos,
                "is_link_correct": is_link_correct,
                "direction_correct": direction_correct,
                "type_correct": type_correct,
                "confidence": pred.confidence,
                "bad_case_type": bad_case_type,
                "gold_source": gold.source if gold else None,
                "gold_target": gold.target if gold else None,
                "predicted_source": pred.source,
                "predicted_target": pred.target,
                "gold_type": gold.relation_type if gold else None,
                "predicted_type": pred.relation_type
            })

        # Calculate metrics
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        direction_accuracy = correct_direction / linkage_tps if linkage_tps > 0 else 1.0
        type_accuracy = correct_type / linkage_tps if linkage_tps > 0 else 1.0

        return {
            "counts": {
                "true_positive": tp,
                "true_negative": tn,
                "false_positive": fp,
                "false_negative": fn,
                "linkage_tps": linkage_tps,
                "correct_direction": correct_direction,
                "correct_type": correct_type
            },
            "metrics": {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "direction_accuracy": direction_accuracy,
                "type_accuracy": type_accuracy
            },
            "type_stats": type_stats,
            "details": details
        }
