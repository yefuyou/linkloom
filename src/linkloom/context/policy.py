"""Small model allocation and evidence-group contracts, independent of Runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable


def estimate_tokens(text: str) -> int:
    """Multilingual heuristic; never claim provider token accuracy or a bound.

    ASCII costs roughly one token per four characters. Non-ASCII uses half its
    UTF-8 byte length (e.g. 1.5 per common Han character), avoiding len/4's large
    Chinese underestimate. Callers can inject a model's actual tokenizer.
    """
    return math.ceil(sum(0.25 if ord(c) < 128 else len(c.encode('utf-8')) / 2 for c in text))


@dataclass(frozen=True, slots=True)
class RequestUsage:
    """Measured reservation for everything outside this assembled text.

    Include user/tool/history/schema envelopes and serialization overhead. The
    caller owns the final provider request measurement, including output tokens.
    """

    tokens: int = 0
    bytes: int = 0

    def __post_init__(self) -> None:
        for value in (self.tokens, self.bytes):
            if type(value) is not int or value < 0:
                raise ValueError('request usage must contain non-negative integers')


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    model_id: str = 'unspecified'
    context_window_tokens: int = 8192
    output_reserve_tokens: int = 1024
    max_request_bytes: int = 96 * 1024
    max_candidates: int = 256
    token_counter: Callable[[str], int] = estimate_tokens
    token_count_kind: str = 'MULTILINGUAL_HEURISTIC_V1'

    def __post_init__(self) -> None:
        for value in (self.context_window_tokens, self.max_request_bytes, self.max_candidates):
            if type(value) is not int or value <= 0:
                raise ValueError('policy limits must be positive integers')
        if type(self.output_reserve_tokens) is not int or not 0 <= self.output_reserve_tokens < self.context_window_tokens:
            raise ValueError('invalid output reserve')
        if not self.model_id or not self.token_count_kind or not callable(self.token_counter):
            raise ValueError('model identity, counter and counter kind are required')

    def count(self, text: str) -> int:
        value = self.token_counter(text)
        if type(value) is not int or value < 0 or (text and value == 0):
            raise ValueError('token counter must return a positive integer for nonempty text')
        return value


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    """An atomic chain or support/counterevidence set declared by the caller.

    Groups must be disjoint; put shared passages and all their subgoals in one
    group. Labels are planning hints, not proof of relevance or source truth.
    """

    group_id: str
    evidence_ids: tuple[str, ...]
    subgoal_ids: tuple[str, ...] = ()
    conflict: bool = False

    def __post_init__(self) -> None:
        if type(self.evidence_ids) is not tuple or type(self.subgoal_ids) is not tuple or type(self.conflict) is not bool:
            raise ValueError('group hints require immutable ID tuples and boolean conflict flag')
        if not self.evidence_ids:
            raise ValueError('group identity and evidence IDs are required')
        if any(not isinstance(x, str) or not x.strip() or len(x) > 256
               for x in (self.group_id, *self.evidence_ids, *self.subgoal_ids)):
            raise ValueError('group hint IDs must be nonempty text of at most 256 characters')
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError('duplicate evidence in group')


@dataclass(frozen=True, slots=True)
class SubgoalCoverage:
    subgoal_id: str
    complete: bool
    missing_evidence_ids: tuple[str, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class EvidenceSelectionResult:
    selected_ids: tuple[str, ...]
    selected_group_ids: tuple[str, ...]
    coverage: tuple[SubgoalCoverage, ...]
    inspected_candidates: int
    omitted_candidates: int


@dataclass(frozen=True, slots=True)
class ContextBudgetReport:
    model_id: str
    token_count_kind: str
    effective_tokens: int
    effective_bytes: int
    reserved_tokens: int
    reserved_bytes: int
    output_reserve_tokens: int
    used_tokens: int
    used_bytes: int

    @property
    def token_utilization(self) -> float:
        return self.used_tokens / self.effective_tokens

    @property
    def byte_utilization(self) -> float:
        return self.used_bytes / self.effective_bytes
