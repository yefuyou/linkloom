"""Extend the existing offline fixture with distinct slots and two workspaces."""
from uuid import uuid4

from unified_ask_fixture import (
    ATLAS, OTHER, OfflineSemanticProvider, UnifiedAskFixture, claim,
)
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.semantic_ingestion.relations import FrozenRelationResolver


class DecisionDiscoveryFixture(UnifiedAskFixture):
    def _seed_synthetic_data(self):
        super()._seed_synthetic_data()
        claims = []
        for subject, relation, value, kind in (
            ("Launch", "has budget", "50000", "DECISION"),
            ("Support", "uses supplier", "Riverton", "DECISION"),
            ("Expansion", "uses supplier", "Northstar", "DECISION"),
            ("Research", "uses supplier", "Mallow", "PROPOSAL"),
        ):
            item = claim(value, claim_type=kind)
            item.update(subject=subject, relation=relation)
            claims.append(item)
        self.app.relation_resolver = FrozenRelationResolver(
            ("uses supplier", "has budget"), aliases={"合作方": "uses supplier"})
        self.app.extractor = ProviderNeutralSemanticExtractor(
            OfflineSemanticProvider(claims), provider_id="offline-discovery",
            model_id="offline-discovery", max_attempts=1,
            request_guard=lambda request: ProviderRequestAuthorization.approve(
                request, guard_id="offline-discovery/v1", reservation_id=uuid4().hex,
                estimated_cost_upper_bound_usd=0.0), request_guard_id="offline-discovery/v1")
        self._import_and_review(name="launch-budget.md",
            content="[2026-10-02T10:00:00Z] Alice: We decided that Launch has a budget of 50000.",
            value="50000", approve=True)
        self.select_workspace(OTHER)
        self._import_and_review(name="support-supplier.md",
            content="[2026-10-03T10:00:00Z] Dana: We decided to use Riverton as the supplier for Support.",
            value="Riverton", approve=True)
        self._import_and_review(name="expansion-supplier.md",
            content="[2026-10-04T10:00:00Z] Dana: We decided to use Northstar as the supplier for Expansion.",
            value="Northstar", approve=True)
        self._import_and_review(name="research-proposal.md",
            content="[2026-10-05T10:00:00Z] Dana: Research proposes Mallow as its supplier.",
            value="Mallow", approve=False)
        self.select_workspace(ATLAS)
