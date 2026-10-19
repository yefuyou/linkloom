"""Logical context filesystem contracts."""

from linkloom.context.assembler import (
    ContextAssembler,
    ContextBudget,
    ContextBundle,
    ContextDropReason,
    ContextSourceType,
    DroppedContextItem,
    SelectedContextItem,
)
from linkloom.context.manifest import ContextManifest
from linkloom.context.policy import ContextPolicy, EvidenceGroup, EvidenceSelectionResult, RequestUsage
from linkloom.context.recovery import EvidenceRecovery, EvidenceRef, SourceSnapshot
from linkloom.context.working import WorkingContext, Compactor, compact_working_context
from linkloom.context.models import (
    ChangeType,
    ContextResource,
    FileChangeEvent,
    ManifestChange,
    ResourceType,
    SourceInventoryEntry,
    normalize_logical_path,
    parent_logical_path,
)

__all__ = [
    "ChangeType",
    "ContextAssembler",
    "ContextBudget",
    "ContextBundle",
    "ContextManifest",
    "ContextPolicy",
    "EvidenceGroup",
    "EvidenceSelectionResult",
    "RequestUsage",
    "EvidenceRecovery",
    "EvidenceRef",
    "SourceSnapshot",
    "WorkingContext",
    "Compactor",
    "compact_working_context",
    "ContextResource",
    "ContextDropReason",
    "ContextSourceType",
    "DroppedContextItem",
    "FileChangeEvent",
    "ManifestChange",
    "ResourceType",
    "SourceInventoryEntry",
    "SelectedContextItem",
    "normalize_logical_path",
    "parent_logical_path",
]
