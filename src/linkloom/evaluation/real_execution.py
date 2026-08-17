"""Real provider inference execution and evidence validation."""

import hashlib
import json
import uuid
from typing import Any, Dict, List, Tuple

from .dataset import InferenceDataset
from .real_provider import AgyCliAdapter, InferenceRequest, ProviderResponse
from .bad_cases import create_bad_case, BadCaseCategory, BadCaseSeverity


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RealProviderRunner:
    def __init__(self, adapter: AgyCliAdapter):
        self.adapter = adapter

    def run_inference(
        self, dataset: InferenceDataset, run_id: str
    ) -> Tuple[List[Dict[str, Any]], List[Any], List[Dict[str, Any]], Dict[str, Any]]:
        predictions: List[Dict[str, Any]] = []
        bad_cases: List[Any] = []
        evidence_validations: List[Dict[str, Any]] = []
        
        usage_total = {
            "provider_name": "gemini_cli",
            "model": self.adapter.model,
            "request_count": 0,
            "retry_count": 0,
            "duration_ms": 0,
            "input_tokens": None,
            "output_tokens": None,
            "estimated_cost": None
        }
        
        from datetime import datetime, timezone
        created_at = datetime.now(timezone.utc).isoformat()
        provider_info = {
            "name": "gemini_cli",
            "model": self.adapter.model,
            "prompt_version": "v1"
        }

        docs = dataset.get_inference_documents()
        doc_by_path = {d.path: d.content for d in docs}

        # --- Batch 1: Topic tasks ---
        topic_requests = []
        topic_req_ids = []
        for i, doc in enumerate(docs, 1):
            prompt = (
                "Analyze the provided document and determine if it should link to the agent evaluation framework. "
                "The topic is whether the document contributes to the evaluation of AI agents."
            )
            req = InferenceRequest(
                source_id=doc.path,
                target_id=doc.path,
                prompt=prompt,
                documents={doc.path: doc_by_path[doc.path]}
            )
            topic_requests.append(req)
            topic_req_ids.append((f"pred_p65_topic_{i:04d}", doc.path, doc.path, {doc.path}))

        if topic_requests:
            base_prompt = topic_requests[0].prompt
            topic_responses = []
            for chunk in self._chunk_requests(topic_requests, base_prompt):
                chunk_responses = self.adapter.predict_batch(chunk)
                if chunk_responses:
                    self._accumulate_usage(usage_total, chunk_responses[0])
                topic_responses.extend(chunk_responses)
                
            for req, resp, (pred_id, src, tgt, valid_paths) in zip(topic_requests, topic_responses, topic_req_ids):
                if resp.error_type:
                    bad_cases.append(self._make_error_case(src, tgt, resp.error_type))
                    predictions.append({
                        "prediction_id": pred_id,
                        "run_id": run_id,
                        "task_type": "topic",
                        "input_refs": [src],
                        "output": {"error_type": resp.error_type},
                        "provider": provider_info,
                        "schema_status": "invalid",
                        "created_at": created_at,
                        "evidence_refs": []
                    })
                elif resp.record:
                    rec_dict, ev_validations, ev_bad_cases = self._process_record(
                        "topic", pred_id, resp.record, src, tgt, valid_paths, doc_by_path,
                        run_id=run_id, created_at=created_at, provider_info=provider_info
                    )
                    predictions.append(rec_dict)
                    evidence_validations.extend(ev_validations)
                    bad_cases.extend(ev_bad_cases)

        # --- Batch 2: Relation tasks ---
        relation_requests = []
        relation_req_ids = []
        for i, pair in enumerate(dataset.get_inference_pairs(), 1):
            prompt = (
                "Analyze the two provided documents and determine if there is a meaningful conceptual relation between them. "
                "Specify the direction and relation_type if applicable."
            )
            req = InferenceRequest(
                source_id=pair.source_id,
                target_id=pair.target_id,
                prompt=prompt,
                documents={pair.source_id: doc_by_path[pair.source_id], pair.target_id: doc_by_path[pair.target_id]}
            )
            relation_requests.append(req)
            relation_req_ids.append((f"pred_p65_relation_{i:04d}", pair.source_id, pair.target_id, {pair.source_id, pair.target_id}))

        if relation_requests:
            base_prompt = relation_requests[0].prompt
            relation_responses = []
            for chunk in self._chunk_requests(relation_requests, base_prompt):
                chunk_responses = self.adapter.predict_batch(chunk)
                if chunk_responses:
                    self._accumulate_usage(usage_total, chunk_responses[0])
                relation_responses.extend(chunk_responses)

            for req, resp, (pred_id, src, tgt, valid_paths) in zip(relation_requests, relation_responses, relation_req_ids):
                if resp.error_type:
                    bad_cases.append(self._make_error_case(src, tgt, resp.error_type))
                    predictions.append({
                        "prediction_id": pred_id,
                        "run_id": run_id,
                        "task_type": "relation",
                        "input_refs": [src, tgt],
                        "output": {"error_type": resp.error_type},
                        "provider": provider_info,
                        "schema_status": "invalid",
                        "created_at": created_at,
                        "evidence_refs": []
                    })
                elif resp.record:
                    rec_dict, ev_validations, ev_bad_cases = self._process_record(
                        "relation", pred_id, resp.record, src, tgt, valid_paths, doc_by_path,
                        run_id=run_id, created_at=created_at, provider_info=provider_info
                    )
                    predictions.append(rec_dict)
                    evidence_validations.extend(ev_validations)
                    bad_cases.extend(ev_bad_cases)

        return predictions, bad_cases, evidence_validations, usage_total

    def _chunk_requests(self, requests: List[InferenceRequest], base_prompt: str, char_limit: int = 5000) -> List[List[InferenceRequest]]:
        chunks = []
        current_chunk = []
        current_len = 0
        fixed_overhead = len(base_prompt) + 500
        
        for req in requests:
            payload = {
                "task": "topic" if req.source_id == req.target_id else "relation",
                "source_id": req.source_id,
                "target_id": req.target_id,
                "documents": req.documents
            }
            req_str = json.dumps(payload, ensure_ascii=False)
            req_len = len(req_str) + 50  # buffer for comma and spacing
            
            if not current_chunk:
                current_chunk.append(req)
                current_len = fixed_overhead + req_len
            else:
                if current_len + req_len > char_limit:
                    chunks.append(current_chunk)
                    current_chunk = [req]
                    current_len = fixed_overhead + req_len
                else:
                    current_chunk.append(req)
                    current_len += req_len
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def _accumulate_usage(self, total: Dict[str, Any], resp: ProviderResponse) -> None:
        usage = resp.usage
        total["request_count"] += usage.request_count
        total["retry_count"] += usage.retry_count
        total["duration_ms"] += usage.duration_ms
        if usage.prompt_tokens is not None:
            total["input_tokens"] = (total.get("input_tokens") or 0) + usage.prompt_tokens
        if usage.completion_tokens is not None:
            total["output_tokens"] = (total.get("output_tokens") or 0) + usage.completion_tokens

    def _make_error_case(self, source_id: str, target_id: str, error_type: str) -> Any:
        cat = BadCaseCategory.MALFORMED_OUTPUT if error_type in ("invalid_json", "schema_invalid") else BadCaseCategory.PROVIDER_ERROR
        return create_bad_case(
            source_id,
            target_id,
            cat,
            BadCaseSeverity.HIGH,
            f"Provider inference failed with error: {error_type}",
            [],
        )

    def _process_record(
        self, kind: str, pred_id: str, record: Any, source_id: str, target_id: str, valid_paths: set[str], doc_by_path: Dict[str, str], run_id: str, created_at: str, provider_info: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Any]]:
        
        valid_refs = []
        ev_validations = []
        bad_cases = []

        for quote in record.evidence_refs:
            if not isinstance(quote, str):
                continue
            
            found = False
            for path in valid_paths:
                content = doc_by_path[path]
                if quote in content:
                    q_sha = _sha256_text(quote)
                    valid_refs.append({"note_path": path, "quote_sha256": q_sha})
                    ev_validations.append({
                        "prediction_id": pred_id,
                        "note_path": path,
                        "quote_sha256": q_sha,
                        "valid": True,
                        "reason": "exact_match"
                    })
                    found = True
                    break
            
            if not found:
                q_sha = _sha256_text(quote)
                ev_validations.append({
                    "prediction_id": pred_id,
                    "note_path": "unknown",
                    "quote_sha256": q_sha,
                    "valid": False,
                    "reason": "substring_not_found"
                })
                bad_cases.append(create_bad_case(
                    source_id, target_id, BadCaseCategory.EVIDENCE_INVALID, BadCaseSeverity.HIGH,
                    "Evidence quote not found in source documents", []
                ))

        if kind == "topic":
            rec_dict = {
                "prediction_id": pred_id,
                "run_id": run_id,
                "task_type": "topic",
                "input_refs": [source_id],
                "output": {
                    "should_link": record.should_link,
                    "confidence": record.confidence,
                },
                "provider": provider_info,
                "schema_status": "valid",
                "created_at": created_at,
                
                "kind": "topic",
                "document_id": source_id,
                "should_link": record.should_link,
                "confidence": record.confidence,
                "evidence_refs": valid_refs
            }
        else:
            source = source_id if record.direction == "forward" else target_id if record.direction == "backward" else None
            target = target_id if record.direction == "forward" else source_id if record.direction == "backward" else None
            rec_dict = {
                "prediction_id": pred_id,
                "run_id": run_id,
                "task_type": "relation",
                "input_refs": [source_id, target_id],
                "output": {
                    "relation_type": record.relation_type,
                    "direction": record.direction,
                    "should_link": record.should_link,
                    "confidence": record.confidence,
                },
                "provider": provider_info,
                "schema_status": "valid",
                "created_at": created_at,

                "kind": "relation",
                "source_id": source_id,
                "target_id": target_id,
                "should_link": record.should_link,
                "source": source,
                "target": target,
                "relation_type": record.relation_type,
                "confidence": record.confidence,
                "evidence_refs": valid_refs
            }

        return rec_dict, ev_validations, bad_cases
