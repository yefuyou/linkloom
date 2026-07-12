import yaml
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

class DatasetConfig(BaseModel):
    name: str
    fixture_path: str
    documents_root: str

class ProviderConfig(BaseModel):
    type: str
    scenario: str = "perfect"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = "gpt-4o-mini"

class InferenceConfig(BaseModel):
    topic_enabled: bool
    relation_enabled: bool
    max_retries: int
    concurrency: int

class BudgetConfig(BaseModel):
    max_requests_per_run: int
    max_cost_per_run_usd: float
    stop_on_budget_exceeded: bool

class OutputConfig(BaseModel):
    root: str

class CacheConfig(BaseModel):
    enabled: bool

class EvalConfig(BaseModel):
    dataset: DatasetConfig
    provider: ProviderConfig
    inference: InferenceConfig
    budget: BudgetConfig
    output: OutputConfig
    cache: CacheConfig

    @classmethod
    def load_from_yaml(cls, path: str | Path) -> 'EvalConfig':
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)
