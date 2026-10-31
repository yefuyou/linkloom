"""Explicit Host-owned meeting mode selection; legacy callers remain unchanged."""
from dataclasses import asdict

from linkloom.semantic_ingestion.meeting_tasks_v3 import TaskExtractionConfig


def build_meeting_extractor(adapter, *, model_id, config, **kwargs):
    if not isinstance(config, TaskExtractionConfig):
        raise TypeError("Trusted TaskExtractionConfig required")
    if config.task_extraction_mode == "meeting_v3":
        from linkloom.semantic_ingestion.meeting_tasks_v3 import MeetingTaskExtractor
        return MeetingTaskExtractor(adapter, model_id=model_id, config=config, **kwargs)
    if config.task_extraction_mode == "meeting_v2":
        from linkloom.semantic_ingestion.meeting_tasks_v2 import MeetingTaskExtractor, TaskExtractionConfig as V2Config
        return MeetingTaskExtractor(adapter, model_id=model_id, config=V2Config(**asdict(config)), **kwargs)
    raise ValueError("legacy uses its existing task_observation path; no meeting fallback")
