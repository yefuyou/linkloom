from typing import List, Dict, Optional
from pydantic import BaseModel, Field, model_validator

class DocumentInput(BaseModel):
    note_path: str
    content: str
    content_hash: str

class TopicPrediction(BaseModel):
    note_path: str
    predicted_topic_ids: List[str]
    should_link_to_agent_evaluation: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: List[str]
    reason: str

class RelationEvidence(BaseModel):
    source: List[str]
    target: List[str]

class RelationPrediction(BaseModel):
    left_note_path: str
    right_note_path: str
    should_link: bool
    source: Optional[str] = None
    target: Optional[str] = None
    relation_type: Optional[str] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: RelationEvidence
    reason: str

    @model_validator(mode='after')
    def validate_relation_fields(self) -> 'RelationPrediction':
        if self.should_link:
            if not self.source or not self.target or not self.relation_type:
                raise ValueError("source, target, and relation_type cannot be null when should_link is True")
        else:
            if self.source is not None or self.target is not None or self.relation_type is not None:
                raise ValueError("source, target, and relation_type must be null when should_link is False")
        return self
