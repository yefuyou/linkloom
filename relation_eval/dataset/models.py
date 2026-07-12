from typing import List, Optional
from pydantic import BaseModel, Field

class GoldNote(BaseModel):
    note_path: str
    expected_topic_ids: List[str]
    note_type: str
    should_link_to_agent_evaluation: bool
    evidence: List[str]
    difficulty: str
    rationale: str

class GoldPairEvidenceDetail(BaseModel):
    source: List[str]
    target: List[str]

class GoldPair(BaseModel):
    source: str
    target: str
    relation_type: str
    evidence: GoldPairEvidenceDetail
    rationale: str
    should_link: bool = True  # We will normalize this!

class GoldDataset(BaseModel):
    notes: List[GoldNote]
    expected_pairs: List[GoldPair]
    dataset_version: str
