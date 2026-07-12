from typing import List, Dict, Any
from relation_eval.schemas import TopicPrediction
from relation_eval.dataset.models import GoldDataset

class TopicScorer:
    def __init__(self, gold_dataset: GoldDataset):
        self.gold_map = {n.note_path: n for n in gold_dataset.notes}

    def score(self, predictions: List[TopicPrediction]) -> Dict[str, Any]:
        tp = 0
        tn = 0
        fp = 0
        fn = 0
        
        doc_details = []

        for pred in predictions:
            path = pred.note_path
            gold = self.gold_map.get(path)
            if not gold:
                # If prediction has a document not in gold, treat as invalid or skip
                continue

            gold_pos = gold.should_link_to_agent_evaluation
            pred_pos = pred.should_link_to_agent_evaluation

            is_correct = (gold_pos == pred_pos)
            
            # Determine bad case type if incorrect
            bad_case_type = None
            if not is_correct:
                if pred_pos:
                    fp += 1
                    bad_case_type = "TOPIC_FALSE_POSITIVE"
                else:
                    fn += 1
                    bad_case_type = "TOPIC_FALSE_NEGATIVE"
            else:
                if pred_pos:
                    tp += 1
                else:
                    tn += 1

            doc_details.append({
                "note_path": path,
                "gold_label": gold_pos,
                "predicted_label": pred_pos,
                "is_correct": is_correct,
                "confidence": pred.confidence,
                "predicted_evidence": pred.evidence,
                "bad_case_type": bad_case_type
            })

        # Calculate metrics
        accuracy = (tp + tn) / len(predictions) if predictions else 0.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        # FPR = FP / (FP + TN)
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        # FNR = FN / (FN + TP)
        fnr = fn / (fn + tp) if (fn + tp) > 0 else 0.0

        return {
            "counts": {
                "true_positive": tp,
                "true_negative": tn,
                "false_positive": fp,
                "false_negative": fn
            },
            "metrics": {
                "accuracy": accuracy,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "false_positive_rate": fpr,
                "false_negative_rate": fnr
            },
            "details": doc_details
        }
